"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const token = document.querySelector('meta[name="panel-token"]').content;
  const number = new Intl.NumberFormat("es-CO");
  const localDate = new Intl.DateTimeFormat("es-CO", {
    dateStyle: "short", timeStyle: "short", timeZone: "America/Bogota",
  });
  const statusLabels = {
    queued: "En espera", running: "En curso", completed: "Completado",
    partial: "Completado con errores", failed: "Falló", cancelled: "Cancelado",
    unavailable: "Progreso no disponible",
  };
  const actionLabels = {
    discover: "Descubrimiento de URLs", scrape: "Extracción de artículos",
    reclean: "Limpieza de respaldos", "import-cache": "Importación local",
  };
  const state = {
    summary: {}, items: [], page: 1, pages: 1, total: 0,
    currentId: null, detail: null, detailTab: "clean",
    listController: null, detailController: null, searchTimer: null,
    listSequence: 0, detailSequence: 0, jobTimer: null, job: null,
    busy: false, submitting: false, selectionBusy: false, completedJob: null,
    loadedFilters: null,
  };

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  async function api(path, options = {}) {
    const headers = { Accept: "application/json", ...(options.headers || {}) };
    if (options.method && options.method !== "GET") {
      headers["X-Panel-Token"] = token;
      headers["Content-Type"] = "application/json";
    }
    const response = await fetch(path, { credentials: "same-origin", ...options, headers });
    let value;
    try { value = await response.json(); }
    catch { throw new Error(`El servidor no devolvió una respuesta válida (${response.status}).`); }
    if (!response.ok) {
      const error = new Error(value.error || `La operación falló (${response.status}).`);
      error.status = response.status;
      throw error;
    }
    return value;
  }

  function showError(message) {
    $("app-error").textContent = String(message);
    $("app-error").hidden = false;
    $("app-notice").hidden = true;
  }

  function clearError() { $("app-error").hidden = true; }

  function notice(message) {
    $("app-notice").textContent = message;
    $("app-notice").hidden = false;
  }

  function sourceDate(value) {
    if (!value) return "Sin fecha";
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    return match ? `${match[3]}/${match[2]}/${match[1]}` : String(value);
  }

  function capturedDate(value) {
    if (!value) return "Sin fecha";
    const parsed = new Date(value);
    return Number.isNaN(parsed.getTime()) ? String(value) : localDate.format(parsed);
  }

  function safeSourceUrl(value) {
    try {
      const url = new URL(value);
      return ["https:", "http:"].includes(url.protocol) ? url.href : null;
    } catch { return null; }
  }

  function safeWordPressEditorUrl(value) {
    try {
      const url = new URL(value);
      const knownHosts = ["colombianosespana.com", "canadiansinspain.com"];
      if (url.protocol !== "https:" || !knownHosts.includes(url.host) || url.username || url.password
          || url.pathname !== "/wp-admin/post.php" || url.hash
          || !/^[1-9]\d*$/.test(url.searchParams.get("post") || "")
          || url.searchParams.get("action") !== "edit" || [...url.searchParams].length !== 2) return null;
      return url.href;
    } catch { return null; }
  }

  function deploymentLabel(deployment) {
    const labels = {
      draft: "Borrador guardado en WordPress", sending: "Envío en curso",
      running: "Envío en curso", publish: "Entrada publicada", future: "Entrada programada",
      private: "Entrada privada", pending: "Entrada pendiente de revisión",
      failed: "Envío fallido", unknown: "Resultado del envío incierto",
    };
    return deployment ? (labels[deployment.status] || "Estado de envío no disponible") : "Sin envío a WordPress";
  }

  function validRange(from, to) {
    if (from && to && from > to) {
      throw new Error("La fecha inicial no puede ser posterior a la fecha final.");
    }
  }

  function isActive(job) { return Boolean(job && ["queued", "running"].includes(job.status)); }

  function syncControls() {
    const selected = Number(state.summary.selected_count || 0);
    const working = state.busy || state.submitting;
    $("selection-label").textContent = `${number.format(selected)} seleccionado${selected === 1 ? "" : "s"}`;
    $("stat-selected").textContent = number.format(selected);
    $("refresh-selected").disabled = !selected || working || state.selectionBusy;
    $("reclean-selected").disabled = !selected || working || state.selectionBusy;
    $("adapt-selected").disabled = !selected || working || state.selectionBusy;
    $("clear-selection").disabled = !selected || state.selectionBusy;
    $("select-page").disabled = !state.items.length || state.selectionBusy;
    $("select-all-results").disabled = !state.total || state.selectionBusy || !state.loadedFilters;
    $("select-all-results").textContent = `Seleccionar todos los resultados (${number.format(state.total)})`;
    $("scrape-controls").disabled = working;
    $("reclean-all").disabled = working || !Number(state.summary.articles || 0);
    $("use-discovered").disabled = working;
    $("previous-page").disabled = state.page <= 1;
    $("next-page").disabled = state.page >= state.pages || state.total === 0;
    document.querySelectorAll(".article-selection").forEach((checkbox) => {
      checkbox.disabled = state.selectionBusy;
    });
  }

  async function loadSummary() {
    const data = await api("/api/summary");
    state.summary = data;
    $("stat-articles").textContent = number.format(data.articles || 0);
    $("stat-review").textContent = number.format(data.review_required || 0);
    $("stat-words").textContent = number.format(data.words || 0);
    const missingPublication = Number(data.missing_publication_dates || 0);
    const missingModification = Number(data.missing_modification_dates || 0);
    $("date-coverage").textContent = missingPublication || missingModification
      ? `Sin fecha: ${missingPublication} de publicación · ${missingModification} de modificación`
      : "Fechas de publicación y modificación disponibles";
    syncControls();
  }

  function listParameters() {
    const from = $("date-from").value;
    const to = $("date-to").value;
    validRange(from, to);
    return new URLSearchParams({
      q: $("search-query").value.trim(), date_field: $("date-field").value,
      date_from: from, date_to: to, review: $("review-filter").value,
      adaptation: $('adaptation-filter').value,
      selected_only: String($("selected-only").checked), page: String(state.page),
      page_size: "25", sort: $("sort-order").value,
    });
  }

  function resetDetail() {
    state.currentId = null;
    state.detail = null;
    if (state.detailController) state.detailController.abort();
    state.detailSequence += 1;
    $("detail-content").hidden = true;
    $("detail-empty").hidden = false;
    $("detail-empty").replaceChildren(
      node("h2", "Revise el contenido extraído"),
      node("p", "Haga clic en un artículo de la lista para leer su texto, consultar sus fechas y ver qué se excluyó."),
    );
  }

  async function loadList({ reset = false } = {}) {
    if (reset) {
      state.page = 1;
      resetDetail();
    }
    let params;
    try { params = listParameters(); }
    catch (error) { showError(error.message); return; }
    if (state.listController) state.listController.abort();
    state.listController = new AbortController();
    const sequence = ++state.listSequence;
    state.loadedFilters = null;
    syncControls();
    $("article-list").setAttribute("aria-busy", "true");
    $("result-count").textContent = "Buscando…";
    try {
      const data = await api(`/api/articles?${params}`, { signal: state.listController.signal });
      if (sequence !== state.listSequence) return;
      state.items = data.items || [];
      state.total = Number(data.total || 0);
      state.page = Number(data.page || 1);
      state.pages = Math.max(1, Number(data.pages || 1));
      state.loadedFilters = Object.fromEntries(params);
      delete state.loadedFilters.page;
      delete state.loadedFilters.page_size;
      state.loadedFilters.selected_only = state.loadedFilters.selected_only === "true";
      if (state.page > state.pages) {
        state.page = state.pages;
        await loadList();
        return;
      }
      renderList();
    } catch (error) {
      if (error.name !== "AbortError" && sequence === state.listSequence) {
        showError(error.message);
        $("result-count").textContent = "No se pudo cargar la lista";
      }
    } finally {
      if (sequence === state.listSequence) $("article-list").setAttribute("aria-busy", "false");
    }
  }

  function renderList() {
    const fragment = document.createDocumentFragment();
    if (!state.items.length) fragment.append(node("p", "No hay artículos que coincidan con estos filtros.", "list-empty"));
    for (const item of state.items) {
      const row = node("div", null, `article-row${item.id === state.currentId ? " current" : ""}`);
      const checkbox = node("input");
      checkbox.type = "checkbox";
      checkbox.className = "article-selection";
      checkbox.checked = Boolean(item.selected);
      checkbox.setAttribute("aria-label", `Seleccionar ${item.title}`);
      checkbox.addEventListener("change", () => setSelection([item.id], checkbox.checked));
      const open = node("button", null, "article-open");
      open.type = "button";
      if (item.id === state.currentId) open.setAttribute("aria-current", "true");
      open.append(node("span", item.title, "article-name"));
      open.append(node("span", `Publicado ${sourceDate(item.published_at)} · Modificado ${sourceDate(item.modified_at)}`, "article-dates"));
      if (item.last_adapted_at) open.append(node('span', `Última adaptación: ${capturedDate(item.last_adapted_at)} · Bogotá`, 'article-dates'));
      const badges = node("span", null, "article-badges");
      badges.append(node("span", `${number.format(item.word_count || 0)} palabras`));
      if (item.review_required) badges.append(node("span", "Con avisos de revisión", "review-mark"));
      const adaptations = Number(item.generated_count || 0);
      const preparations = Number(item.prepared_count || 0);
      badges.append(node("span", adaptations ? `${adaptations} adaptaciones` : "Sin adaptaciones"));
      if (preparations) badges.append(node("span", `${preparations} preparaciones`, "preparation-mark"));
      open.append(badges);
      if (item.snippet) open.append(node("span", item.snippet, "article-snippet"));
      open.addEventListener("click", () => loadDetail(item.id));
      row.append(checkbox, open);
      fragment.append(row);
    }
    $("article-list").replaceChildren(fragment);
    $("result-count").textContent = `${number.format(state.total)} resultado${state.total === 1 ? "" : "s"}`;
    $("page-label").textContent = `Página ${state.page} de ${state.pages}`;
    syncControls();
  }

  async function selectedIds() {
    const ids = [];
    let page = 1;
    let pages = 1;
    do {
      const data = await api(`/api/articles?selected_only=true&page_size=100&page=${page}`);
      ids.push(...data.items.map((item) => item.id));
      pages = Number(data.pages || 1);
      page += 1;
    } while (page <= pages);
    return ids;
  }

  async function setSelection(ids, selected) {
    if (!ids.length || state.selectionBusy) return;
    state.selectionBusy = true;
    syncControls();
    clearError();
    try {
      const data = await api("/api/selection", {
        method: "POST", body: JSON.stringify({ article_ids: ids, selected }),
      });
      state.summary.selected_count = data.selected_count;
      state.items.forEach((item) => { if (ids.includes(item.id)) item.selected = selected; });
      if (state.detail && ids.includes(state.currentId)) state.detail.article.selected = selected;
      await loadList();
    } catch (error) {
      showError(error.message);
      renderList();
    } finally {
      state.selectionBusy = false;
      syncControls();
    }
  }

  async function selectAllResults() {
    if (state.selectionBusy || !state.loadedFilters) return;
    const filters = { ...state.loadedFilters };
    state.selectionBusy = true;
    syncControls();
    clearError();
    try {
      const result = await api("/api/selection/matching", { method: "POST", body: JSON.stringify(filters) });
      state.summary.selected_count = result.selected_count;
      await loadList();
      notice(`${result.matched_count} resultados incluidos en la selección, entre todas las páginas. Total seleccionado: ${result.selected_count}.`);
    } catch (error) { showError(error.message); }
    finally { state.selectionBusy = false; syncControls(); }
  }

  function metadata(label, value, title) {
    const entry = node("div");
    entry.append(node("dt", label), node("dd", value));
    if (title) entry.lastChild.title = title;
    return entry;
  }

  async function loadDetail(id, { keepTab = false } = {}) {
    if (state.detailController) state.detailController.abort();
    state.detailController = new AbortController();
    const sequence = ++state.detailSequence;
    state.currentId = id;
    if (!keepTab) state.detailTab = "clean";
    state.detail = null;
    renderList();
    $("detail-content").hidden = true;
    $("detail-empty").hidden = false;
    $("detail-empty").replaceChildren(node("p", "Cargando artículo…"));
    try {
      const detail = await api(`/api/articles/${encodeURIComponent(id)}`, { signal: state.detailController.signal });
      if (sequence !== state.detailSequence) return;
      state.detail = detail;
      const article = detail.article;
      $("detail-title").textContent = article.title;
      $("export-link").href = `/api/articles/${encodeURIComponent(id)}/export`;
      const source = safeSourceUrl(article.canonical_url || article.source_url);
      $("source-link").hidden = !source;
      if (source) {
        $("source-link").href = source;
        $("source-link").title = source;
      }
      $("article-metadata").replaceChildren(
        metadata("Publicación original", sourceDate(article.published_at), article.published_at),
        metadata("Modificación original", sourceDate(article.modified_at), article.modified_at),
        metadata("Extracción de versión · Bogotá", capturedDate(article.captured_at), article.captured_at),
        metadata("Idioma", article.language || "Sin dato"),
        metadata("Palabras conservadas", number.format(article.word_count || 0)),
        metadata("Versiones guardadas", number.format(article.version_count || (detail.versions || []).length)),
      );
      const flags = detail.quality_flags || [];
      const requiresReview = Boolean(article.review_required || flags.length);
      $("detail-quality").textContent = requiresReview
        ? "Contiene avisos de revisión. Consulte Exclusiones para ver las omisiones y su contexto antes de adaptar el artículo."
        : "Sin avisos de limpieza pendientes. Este estado no verifica la exactitud de la información original.";
      $("detail-quality").classList.toggle("clear", !requiresReview);
      $("detail-content").hidden = false;
      $("detail-empty").hidden = true;
      renderReading();
    } catch (error) {
      if (error.name !== "AbortError" && sequence === state.detailSequence) {
        $("detail-empty").replaceChildren(node("h2", "No se pudo abrir el artículo"), node("p", error.message));
        showError(error.message);
      }
    }
  }

  function renderReading() {
    if (!state.detail) return;
    document.getElementById("trial-results")?.remove();
    const detail = state.detail;
    document.querySelectorAll("[data-detail-tab]").forEach((button) => {
      const active = button.dataset.detailTab === state.detailTab;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    $("detail-reading").setAttribute("aria-labelledby", `detail-tab-${state.detailTab}`);
    let value;
    let help;
    switch (state.detailTab) {
      case "original":
        value = detail.original_text;
        help = "Texto del cuerpo original, antes de la limpieza. Puede contener autoría, promociones y navegación que no se reutilizan.";
        break;
      case "events": {
        const events = detail.events || [];
        value = events.length ? events.map((event, index) => {
          const previous = event.prev_block_key || "Inicio";
          const next = event.next_block_key || "Final";
          return `${index + 1}. ${event.reason}\nRequiere revisión: ${event.requires_review ? "Sí" : "No"}\nEntre bloques: ${previous} → ${next}\n\n${event.original_text || event.text || "(Sin texto visible; por ejemplo, una imagen o un control.)"}`;
        }).join("\n\n────────────────────────────────────────\n\n") : "No se registraron exclusiones en esta versión.";
        help = "Auditoría de lo retirado. Este texto permanece fuera de la exportación para adaptación; las omisiones informativas se señalan para revisión.";
        break;
      }
      case "blocks":
        value = JSON.stringify(detail.blocks || [], null, 2);
        help = "Bloques en orden de lectura, con jerarquía, tablas, listas y preguntas frecuentes conservadas.";
        break;
      case "links":
        value = JSON.stringify(detail.links || [], null, 2);
        help = "Inventario de enlaces y decisión de conservación. Las referencias internas no se convierten en enlaces de los sitios de destino.";
        break;
      case "versions":
        value = JSON.stringify(detail.versions || [], null, 2);
        help = "Historial de versiones del artículo. La vista de lectura muestra la versión actual.";
        break;
      case "adaptations": {
        const history = detail.adaptations || {};
        const items = history.items || [];
        value = history.message || "Todavía no hubo adaptaciones.";
        const trials = (history.trials || []).filter((trial) => !trial.deleted_at);
        if (trials.length) {
          const trialLabels = {
            completed: "Generada · pendiente de revisión editorial", incomplete: "Incompleta",
            failed: "Fallida", unknown: "Resultado incierto · sin reintento automático", running: "En curso",
          };
          value += "\n\nPRUEBAS DE ADAPTACIÓN\n\n" + trials.map((trial) => [
            `Prueba #${trial.id} · ${trial.configuration_name} · ${trial.destination_name}`,
            `Estado: ${trialLabels[trial.status] || trial.status}`,
            `Modelo: ${trial.model} · ${capturedDate(trial.created_at)}`,
            `Configuración v${trial.configuration_version} · Versión de origen #${trial.source_version_id}`,
            ...trialConsumptionLines(trial),
            `WordPress · último envío registrado: ${deploymentLabel(trial.deployment)}. El estado actual se comprueba al volver a desplegar.`,
          ].join("\n")).join("\n\n");
          renderTrialResults(trials);
        }
        if (items.length) {
          value += "\n\nPREPARACIONES LOCALES · NO GENERADAS\n\n" + items.map((item) => {
            const configuration = item.configuration_name || item.configuration_snapshot?.name || `Configuración #${item.configuration_id}`;
            const destination = item.destination_name || item.destination_snapshot?.name || "Destino guardado";
            const version = item.configuration_version || item.configuration_version_number || item.configuration_version_id;
            const promptVersion = item.prompt_version || item.prompt_version_number || item.prompt_version_id;
            const flags = [];
            if (item.source_changed) flags.push("El artículo tiene una versión más reciente.");
            if (item.configuration_changed) flags.push("La configuración ha cambiado.");
            if (item.destination_changed) flags.push("El destino ha cambiado.");
            if (item.newer_prompt_available) flags.push("Hay una versión más reciente del prompt; esta preparación conserva la anterior.");
            return [
              `${configuration} · ${destination}`,
              `Estado: ${item.status === "cancelled" ? "Cancelada" : "Preparada · sin generar"}`,
              `Configuración v${version || "—"} · Prompt v${promptVersion || "—"} · Versión de origen #${item.source_version_id || item.article_version_id || "—"}`,
              `Preparación #${item.plan_id || item.id || "—"} · ${capturedDate(item.created_at)}`,
              `Modelo previsto: ${item.provider || "—"} · ${item.model || "por definir"}`,
              ...flags,
            ].join("\n");
          }).join("\n\n────────────────────────────────────────\n\n");
        }
        help = "Las adaptaciones conservan su resultado para revisión editorial. Los resultados incompletos no cuentan como adaptaciones. Guardar una preparación no genera contenido; «Generar con API» inicia explícitamente el lote seleccionado.";
        break;
      }
      default:
        value = detail.clean_text;
        help = "Texto conservado en la versión actual, presentado sin el diseño del sitio original.";
    }
    $("reading-help").textContent = help;
    $("article-text").textContent = value || "Sin contenido disponible.";
    $("article-text").scrollTop = 0;
  }

  function trialConsumptionLines(trial) {
    const summary = trial.usage_summary;
    if (!summary) return ['Consumo completo pendiente de consultar. Actualice el panel.'];
    const usd = (value) => value == null ? 'no calculable' : `USD ${Number(value).toFixed(6)}`;
    const range = (value) => value.estimated_cost_usd_min === value.estimated_cost_usd_max
      ? usd(value.estimated_cost_usd_min)
      : `${usd(value.estimated_cost_usd_min)}–${usd(value.estimated_cost_usd_max)}`;
    const lines = summary.records.flatMap((record) => {
      const u = record.usage_fields;
      return [
        `${record.stage === 'assessment' ? 'Evaluación e investigación' : 'Redacción'}: entrada ${u.entrada ?? 'sin dato'} · salida ${u.salidaTotal ?? 'sin dato'} · total ${u.tokensTotales ?? 'sin dato'} tokens`,
        `  Caché leída ${u.entradaCache ?? 'sin dato'} · escrita ${u.escrituraCache ?? 'sin dato'} · razonamiento ${u.razonamiento ?? 'sin dato'} · búsquedas completas ${record.web_search.completed_searches} · coste estimado ${range(record.cost)}`,
      ];
    });
    const totals = summary.totals, u = totals.usage_fields;
    lines.push(`TOTAL ${summary.complete ? 'DE LA PRUEBA' : 'PARCIAL'} (${totals.calls} solicitudes): entrada ${u.entrada} · salida ${u.salidaTotal} · total ${u.tokensTotales} tokens`,
      `COSTE TOTAL ESTIMADO: ${range(totals)}. Incluye las búsquedas registradas.`,
      'La caché forma parte de la entrada y el razonamiento forma parte de la salida; no se suman dos veces.');
    if (!summary.complete) lines.push(summary.notice);
    return lines;
  }

  function renderTrialResults(trials) {
    const container = node("div");
    container.id = "trial-results";
    for (const trial of trials) {
      if (trial.deleted_at) continue;
      container.append(node("h3", `Adaptación #${trial.id} · ${trial.destination_name}`));
      const editorUrl = safeWordPressEditorUrl(trial.deployment?.editor_url);
      if (editorUrl) {
        const paragraph = node("p");
        const link = node("a", `Abrir entrada de la prueba #${trial.id} en WordPress`);
        link.href = editorUrl;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        paragraph.append(link);
        container.append(paragraph);
      }
      const controls = node("div", undefined, "adaptation-actions");
      const blocked = ["running", "unknown"].includes(trial.status) || ["running", "sending", "unknown"].includes(trial.deployment?.status);
      const edit = node("button", "Editar texto");
      edit.type = "button";
      edit.dataset.adaptationAction = "edit";
      edit.dataset.unavailable = String(blocked || trial.status !== "completed");
      edit.disabled = adaptationBusy || edit.dataset.unavailable === "true";
      edit.addEventListener("click", () => editAdaptation(trial));
      const resend = node("button", "Volver a desplegar");
      resend.type = "button";
      resend.dataset.adaptationAction = "redeploy";
      resend.dataset.unavailable = String(blocked || trial.status !== "completed" || !trial.deployment?.post_id);
      resend.disabled = adaptationBusy || resend.dataset.unavailable === "true";
      resend.addEventListener("click", () => prepareRedeployment(trial));
      const remove = node("button", "Borrar de la base local", "danger-button");
      remove.type = "button";
      remove.dataset.adaptationAction = "delete";
      remove.dataset.unavailable = String(blocked);
      remove.disabled = adaptationBusy || blocked;
      remove.addEventListener("click", () => deleteAdaptation(trial));
      const categories = node('button', 'Revisar categorías');
      categories.type = 'button';
      categories.disabled = blocked;
      categories.addEventListener('click', () => window.ETCategories.openTrial(trial.id));
      controls.append(edit, categories, resend, remove);
      container.append(controls);
      if (trial.edited_at) container.append(node("p", `Texto editado localmente el ${capturedDate(trial.edited_at)} · versión ${trial.edit_version}. Use «Volver a desplegar» para enviar los cambios a WordPress.`));
      else if (trial.html_source === "normalized") container.append(node("p", "Formato HTML ajustado automáticamente. El texto se conserva y la salida original de la API permanece guardada."));
      if (blocked) container.append(node("p", "Hay una operación en curso o incierta. Compruebe su resultado antes de volver a actuar."));
      else if (!trial.deployment?.post_id) container.append(node("p", "El reenvío necesita un primer despliegue con título, imagen y entrada identificados."));
      if (trial.deployment?.error) container.append(node("p", trial.deployment.error));
      const details = node("details");
      details.append(node("summary", `Revisar HTML de la prueba #${trial.id} · ${trial.model}`));
      const content = node("pre", "Abra este detalle para cargar el resultado guardado.", "article-text");
      details.append(content);
      let loaded = false;
      let loading = false;
      details.addEventListener("toggle", async () => {
        if (!details.open || loaded || loading) return;
        loading = true;
        content.textContent = "Cargando resultado guardado…";
        try {
          const result = await api(`/api/generation/trials/${encodeURIComponent(trial.id)}`);
          content.textContent = result.html || "Esta prueba no dejó contenido disponible.";
          loaded = true;
        } catch (error) {
          content.textContent = error.message;
        } finally { loading = false; }
      });
      container.append(details);
    }
    $("detail-reading").append(container);
  }

  let adaptationBusy = false;

  function setAdaptationBusy(value) {
    adaptationBusy = value;
    document.querySelectorAll("[data-adaptation-action]").forEach((button) => {
      button.disabled = value || button.dataset.unavailable === "true";
    });
  }

  async function editAdaptation(trial) {
    if (adaptationBusy) return;
    setAdaptationBusy(true);
    clearError();
    let saved;
    try {
      saved = await api(`/api/generation/trials/${trial.id}`);
    } catch (error) { showError(error.message); }
    finally { setAdaptationBusy(false); }
    if (!saved) return;
    const dialog = node("dialog", undefined, "adaptation-dialog");
    const heading = node("h2", `Editar adaptación #${trial.id}`);
    heading.id = "adaptation-edit-title";
    dialog.setAttribute("aria-labelledby", heading.id);
    dialog.append(heading, node("p", "Edite el texto conservando sus etiquetas HTML (<p>, <h2>, <strong>…). Guardar actualiza la copia local; el envío a WordPress se realiza con «Volver a desplegar»."));
    const label = node("label", "Contenido HTML");
    const content = node("textarea");
    content.id = "adaptation-edit-content";
    content.rows = 20;
    content.cols = 80;
    content.maxLength = 500000;
    content.value = saved.html;
    label.htmlFor = content.id;
    const feedback = node("p");
    feedback.setAttribute("role", "status");
    const controls = node("div", undefined, "adaptation-actions");
    const cancel = node("button", "Cancelar");
    const save = node("button", "Guardar texto");
    cancel.type = save.type = "button";
    controls.append(cancel, save);
    dialog.append(label, content, feedback, controls);
    let saving = false;
    cancel.addEventListener("click", () => dialog.close());
    dialog.addEventListener("cancel", (event) => { if (saving) event.preventDefault(); });
    dialog.addEventListener("close", () => dialog.remove());
    save.addEventListener("click", async () => {
      if (saving) return;
      saving = true;
      save.disabled = cancel.disabled = content.disabled = true;
      setAdaptationBusy(true);
      feedback.textContent = "Guardando texto…";
      let success = false;
      try {
        await api(`/api/generation/trials/${trial.id}/content`, {
          method: "PUT", body: JSON.stringify({ html: content.value, revision: saved.content_revision }),
        });
        success = true;
        notice("Texto guardado en la base local. Puede enviarlo con «Volver a desplegar».");
      } catch (error) { feedback.textContent = error.message; }
      finally {
        saving = false;
        save.disabled = cancel.disabled = content.disabled = false;
        if (success) {
          dialog.close();
          await window.ETPanel.refreshArticles();
        }
        setAdaptationBusy(false);
      }
    });
    document.body.append(dialog);
    dialog.showModal();
    content.focus();
  }

  async function deleteAdaptation(trial) {
    if (adaptationBusy) return;
    if (!window.confirm(`¿Borrar la adaptación #${trial.id} de la base local?\n\nSe eliminarán el HTML y sus ediciones, y la adaptación desaparecerá del panel. Esta acción no se puede deshacer desde el panel.\n\nWordPress, el artículo original y los archivos exportados no cambiarán.`)) return;
    setAdaptationBusy(true);
    clearError();
    try {
      await api(`/api/generation/trials/${trial.id}`, { method: "DELETE", body: JSON.stringify({ confirm: true }) });
      notice("Contenido de la adaptación borrado de la base local. WordPress permanece intacto.");
    } catch (error) { showError(error.message); }
    finally {
      await window.ETPanel.refreshArticles();
      setAdaptationBusy(false);
    }
  }

  async function prepareRedeployment(trial) {
    if (adaptationBusy) return;
    setAdaptationBusy(true);
    clearError();
    notice("Comprobando la entrada, imagen y categorías en WordPress…");
    let preview;
    try {
      preview = await api(`/api/generation/trials/${trial.id}/redeploy-preview`, { method: "POST", body: "{}" });
    } catch (error) { showError(error.message); }
    finally { setAdaptationBusy(false); }
    if (!preview) return;
    $("app-notice").hidden = true;
    const dialog = node("dialog", undefined, "adaptation-dialog");
    const heading = node("h2", "Confirmar despliegue");
    heading.id = "adaptation-dialog-title";
    dialog.setAttribute("aria-labelledby", heading.id);
    dialog.append(heading, node("p", preview.title), node("p", `Destino: ${preview.site_url}`));
    dialog.append(node("p", preview.mode === "create"
      ? "La entrada anterior ya no existe. Se creará un borrador nuevo."
      : `Se actualizará la entrada #${preview.post_id}, conservando su estado: ${preview.remote_status}.`));
    dialog.append(node("p", `Imagen destacada: ${preview.featured_media} · Categorías: ${preview.categories.join(", ") || "Sin categorías"}`));
    preview.warnings.forEach((warning) => dialog.append(node("p", warning)));
    function checkbox(labelText) {
      const label = node("label", undefined, "adaptation-confirmation");
      const input = node("input");
      input.type = "checkbox";
      label.append(input, document.createTextNode(labelText));
      dialog.append(label);
      return input;
    }
    const ctas = preview.pending_ctas ? checkbox("Permitir que este borrador conserve marcadores CTA pendientes de completar.") : null;
    const live = preview.live_update ? checkbox("Entiendo que esta entrada está publicada o programada y autorizo sustituir su contenido.") : null;
    const controls = node("div", undefined, "adaptation-actions");
    const cancel = node("button", "Cancelar");
    const send = node("button", preview.mode === "create" ? "Crear borrador" : "Actualizar entrada");
    cancel.type = send.type = "button";
    controls.append(cancel, send);
    dialog.append(controls);
    const sync = () => { send.disabled = Boolean((ctas && !ctas.checked) || (live && !live.checked)); };
    ctas?.addEventListener("change", sync);
    live?.addEventListener("change", sync);
    sync();
    let sending = false;
    cancel.addEventListener("click", () => dialog.close());
    dialog.addEventListener("cancel", (event) => { if (sending) event.preventDefault(); });
    dialog.addEventListener("close", () => dialog.remove());
    send.addEventListener("click", async () => {
      if (sending || send.disabled) return;
      sending = true;
      send.disabled = cancel.disabled = true;
      setAdaptationBusy(true);
      send.textContent = "Enviando…";
      try {
        const result = await api(`/api/generation/trials/${trial.id}/redeploy`, { method: "POST", body: JSON.stringify({
          request_key: crypto.randomUUID(), revision: preview.revision,
          allow_pending_ctas: Boolean(ctas?.checked), confirm_live: Boolean(live?.checked),
        }) });
        if (result.status === "completed") notice("Despliegue completado. Se reutilizó el texto guardado, sin llamar a OpenAI.");
        else showError(result.error || "El envío está en curso. Consulte su estado en el historial.");
      } catch (error) { showError(`${error.message} Consulte el historial antes de repetir la acción.`); }
      finally {
        sending = false;
        dialog.close();
        await window.ETPanel.refreshArticles();
        setAdaptationBusy(false);
      }
    });
    document.body.append(dialog);
    dialog.showModal();
  }

  function switchMainTab(name) {
    document.querySelectorAll("[data-main-tab]").forEach((button) => {
      const active = button.dataset.mainTab === name;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
    });
    $("review-panel").hidden = name !== "review";
    $("scraping-panel").hidden = name !== "scraping";
    $("generation-panel").hidden = name !== "generation";
    if (name === "scraping") loadRuns().catch((error) => showError(error.message));
    window.dispatchEvent(new CustomEvent("panel:section", { detail: { name } }));
  }

  function syncScrapeScope() {
    const catalog = $("scrape-scope").value === "catalog";
    $("catalog-controls").hidden = !catalog;
    $("urls-control").hidden = catalog;
    $("scrape-pages").disabled = !catalog || $("scrape-all-pages").checked;
    $("discover-urls").disabled = !catalog;
    $("scrape-urls").required = !catalog;
    $("scrape-pages").required = catalog && !$("scrape-all-pages").checked;
  }

  function scrapePayload(action) {
    const scope = $("scrape-scope").value;
    const payload = { action, scope, mode: $("scrape-mode").value };
    if (scope === "catalog") {
      payload.all_pages = $("scrape-all-pages").checked;
      payload.max_pages = Number($("scrape-pages").value || 3);
    } else {
      payload.urls = $("scrape-urls").value.split(/\r?\n/).map((url) => url.trim()).filter(Boolean);
      if (!payload.urls.length) throw new Error("Ingrese al menos una URL de artículo.");
    }
    if (action === "scrape") {
      if ($("scrape-limit").value) payload.limit = Number($("scrape-limit").value);
      payload.date_field = $("scrape-date-field").value;
      const from = $("scrape-date-from").value;
      const to = $("scrape-date-to").value;
      validRange(from, to);
      if (from) payload.date_from = from;
      if (to) payload.date_to = to;
    }
    return payload;
  }

  async function submitJob(payload) {
    if (state.busy || state.submitting) return;
    clearError();
    $("app-notice").hidden = true;
    state.submitting = true;
    syncControls();
    try {
      const job = await api("/api/jobs", { method: "POST", body: JSON.stringify(payload) });
      state.completedJob = null;
      renderJob(job);
      schedulePoll(job.id, 300);
    } catch (error) { showError(error.message); }
    finally { state.submitting = false; syncControls(); }
  }

  async function selectedJob(action) {
    if (state.busy || state.submitting) return;
    try {
      const ids = await selectedIds();
      if (!ids.length) throw new Error("Seleccione al menos un artículo.");
      await submitJob({ action, scope: "selected", article_ids: ids, mode: "refresh" });
    } catch (error) { showError(error.message); }
  }

  function renderJob(job) {
    state.job = job;
    state.busy = isActive(job);
    const progress = job.progress || {};
    $("job-panel").hidden = false;
    $("job-title").textContent = actionLabels[job.action] || "Operación del panel";
    $("job-status").textContent = statusLabels[job.status] || job.status;
    $("job-status").className = `badge ${["completed", "failed", "partial"].includes(job.status) ? job.status : ""}`;
    $("job-message").textContent = job.message || (state.busy ? "Procesando…" : "Operación finalizada.");
    const total = Number(progress.total || 0);
    const processed = Number(progress.processed || 0);
    if (total > 0) {
      $("job-progress").max = total;
      $("job-progress").value = Math.min(processed, total);
    } else if (state.busy) {
      $("job-progress").removeAttribute("value");
    } else {
      $("job-progress").max = 1;
      $("job-progress").value = 1;
    }
    $("job-counts").textContent = [
      `${number.format(processed)}${total ? ` de ${number.format(total)}` : ""} procesados`,
      `${Number(progress.success || 0)} guardados`, `${Number(progress.skipped || 0)} omitidos`,
      `${Number(progress.failed || 0)} errores`, `${Number(progress.out_of_range || 0)} fuera del rango`,
      `${Number(progress.missing_date || 0)} sin la fecha requerida`,
    ].join(" · ");
    $("job-current").textContent = job.current_url || "";
    const errors = job.errors || [];
    $("job-errors-wrap").hidden = !errors.length;
    $("job-errors-title").textContent = `Ver errores (${errors.length})`;
    $("job-errors").textContent = errors.map((error) => typeof error === "string" ? error : `${error.url || ""}\n${error.error || JSON.stringify(error)}`).join("\n\n");
    const urls = (job.result && job.result.urls) || job.discovered_urls || [];
    $("job-urls-wrap").hidden = !urls.length;
    $("job-urls-title").textContent = `URLs descubiertas (${urls.length})`;
    $("job-urls").textContent = urls.join("\n");
    syncControls();
  }

  function schedulePoll(id, delay = 1000) {
    clearTimeout(state.jobTimer);
    state.jobTimer = setTimeout(() => pollJob(id), delay);
  }

  async function pollJob(id) {
    try {
      const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
      renderJob(job);
      if (isActive(job)) { schedulePoll(id); return; }
      if (state.completedJob !== job.id) {
        state.completedJob = job.id;
        const refresh = [loadSummary(), loadList(), loadRuns()];
        if (state.currentId !== null) refresh.push(loadDetail(state.currentId, { keepTab: true }));
        const results = await Promise.allSettled(refresh);
        for (const result of results) if (result.status === "rejected") showError(result.reason.message);
      }
    } catch (error) {
      if (error.status === 404) {
        clearTimeout(state.jobTimer);
        renderJob({ ...state.job, status: "unavailable", current_url: null,
          message: "El progreso de esta operación ya no está disponible; puede ocurrir si se reinicia el panel. Consulte el historial y recargue la página antes de iniciar otra acción." });
        loadRuns().catch((historyError) => showError(historyError.message));
        return;
      }
      showError(`No se pudo consultar el progreso: ${error.message}`);
      schedulePoll(id, 3000);
    }
  }

  async function restoreJob() {
    const result = await api("/api/jobs");
    const jobs = result.items || [];
    const job = jobs.find(isActive) || jobs[0];
    if (job) {
      renderJob(job);
      if (isActive(job)) schedulePoll(job.id);
    }
  }

  async function loadRuns() {
    const result = await api("/api/runs");
    const runs = result.items || [];
    const fragment = document.createDocumentFragment();
    for (const run of runs) {
      const row = node("tr");
      const kind = String(run.kind || run.action || "Operación").replace(/^dashboard_/, "");
      row.append(node("td", `${actionLabels[kind] || kind} · #${run.id}`));
      row.append(node("td", capturedDate(run.started_at || run.created_at)));
      row.append(node("td", statusLabels[run.status] || run.status));
      const stats = run.stats || run.stats_json || {};
      const parsed = typeof stats === "string" ? JSON.parse(stats) : stats;
      const cell = node("td");
      cell.append(node("span", kind === "discover"
        ? `${(parsed.result && parsed.result.urls && parsed.result.urls.length) || 0} URLs descubiertas · ${parsed.failed || 0} errores`
        : `${parsed.success || 0} guardados · ${parsed.skipped || 0} omitidos · ${parsed.failed || 0} errores`));
      const details = node("details");
      details.append(node("summary", "Ver resultado"), node("pre", JSON.stringify(parsed, null, 2), "small-pre"));
      cell.append(details);
      row.append(cell);
      fragment.append(row);
    }
    $("runs-body").replaceChildren(fragment);
    $("runs-empty").hidden = Boolean(runs.length);
  }

  function bindTabs(selector, callback) {
    const buttons = [...document.querySelectorAll(selector)];
    buttons.forEach((button, index) => {
      button.addEventListener("click", () => callback(button));
      button.addEventListener("keydown", (event) => {
        let next;
        if (event.key === "ArrowRight") next = (index + 1) % buttons.length;
        else if (event.key === "ArrowLeft") next = (index - 1 + buttons.length) % buttons.length;
        else if (event.key === "Home") next = 0;
        else if (event.key === "End") next = buttons.length - 1;
        else return;
        event.preventDefault();
        buttons[next].focus();
        callback(buttons[next]);
      });
    });
  }

  function applyFilters() {
    clearTimeout(state.searchTimer);
    clearError();
    loadList({ reset: true });
  }

  $("search-form").addEventListener("submit", (event) => { event.preventDefault(); applyFilters(); });
  $("search-query").addEventListener("input", () => {
    clearTimeout(state.searchTimer);
    state.searchTimer = setTimeout(applyFilters, 300);
  });
  ["date-field", "date-from", "date-to", "review-filter", "sort-order", "selected-only", 'adaptation-filter'].forEach((id) => {
    $(id).addEventListener("change", () => {
      $("date-help").textContent = $('date-field').value === 'last_adapted_at'
        ? 'El rango usa la adaptación completa más reciente que se conserva localmente, en hora de Bogotá. No incluye intentos fallidos ni contenido borrado.'
        : $("date-field").value === "captured_at"
        ? "El rango incluye ambos días y se consulta en la zona horaria de Bogotá. La extracción de versión no es la publicación ni la última comprobación del sitio."
        : "Los extremos del rango se incluyen. Las fechas de publicación y modificación corresponden al sitio original.";
      applyFilters();
    });
  });
  $("reset-filters").addEventListener("click", () => {
    $("search-form").reset();
    $("date-help").textContent = "Los extremos del rango se incluyen. Las fechas de publicación y modificación corresponden al sitio original.";
    applyFilters();
  });
  $("previous-page").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadList(); } });
  $("next-page").addEventListener("click", () => { if (state.page < state.pages) { state.page += 1; loadList(); } });
  $("select-page").addEventListener("click", () => setSelection(state.items.map((item) => item.id), true));
  $("select-all-results").addEventListener("click", selectAllResults);
  $("clear-selection").addEventListener("click", async () => {
    try { await setSelection(await selectedIds(), false); }
    catch (error) { showError(error.message); }
  });
  $("refresh-selected").addEventListener("click", () => selectedJob("scrape"));
  $("reclean-selected").addEventListener("click", () => selectedJob("reclean"));
  bindTabs("[data-main-tab]", (button) => switchMainTab(button.dataset.mainTab));
  bindTabs("[data-detail-tab]", (button) => { state.detailTab = button.dataset.detailTab; renderReading(); });
  $("scrape-scope").addEventListener("change", syncScrapeScope);
  $("scrape-all-pages").addEventListener("change", syncScrapeScope);
  $("scrape-form").addEventListener("submit", (event) => {
    event.preventDefault();
    try { submitJob(scrapePayload("scrape")); }
    catch (error) { showError(error.message); }
  });
  $("discover-urls").addEventListener("click", () => {
    if (!$("scrape-form").reportValidity()) return;
    try { submitJob(scrapePayload("discover")); }
    catch (error) { showError(error.message); }
  });
  $("reclean-all").addEventListener("click", () => submitJob({ action: "reclean", scope: "all" }));
  $("refresh-runs").addEventListener("click", () => loadRuns().catch((error) => showError(error.message)));
  $("use-discovered").addEventListener("click", () => {
    const urls = (state.job && state.job.result && state.job.result.urls) || (state.job && state.job.discovered_urls) || [];
    $("scrape-scope").value = "urls";
    $("scrape-urls").value = urls.join("\n");
    switchMainTab("scraping");
    syncScrapeScope();
    $("scrape-urls").focus();
    notice(`${urls.length} URLs listas en el formulario. Elija las opciones y ejecute la extracción.`);
  });

  syncScrapeScope();
  window.ETPanel = {
    api, selectedIds, showError, notice, capturedDate,
    currentArticleId: () => state.currentId,
    refreshArticles: async () => {
      const tasks = [loadSummary(), loadList()];
      if (state.currentId !== null) tasks.push(loadDetail(state.currentId, { keepTab: true }));
      const results = await Promise.allSettled(tasks);
      for (const result of results) if (result.status === "rejected") showError(result.reason.message);
    },
  };
  Promise.allSettled([loadSummary(), loadList(), loadRuns(), restoreJob()]).then((results) => {
    for (const result of results) if (result.status === "rejected") showError(result.reason.message);
  });
})();
