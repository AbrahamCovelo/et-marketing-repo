"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const panel = window.ETPanel;
  const preparation = window.ETGeneration;
  let catalog = null, loading = null, capabilities = null, capabilityModel = "", capabilitySequence = 0;
  let activeRun = null, pollTimer = null, submitting = false, pendingRequest = null;
  let costQuote = null, quoteIdentity = '', calculatingCost = false;
  const usd = (value) => value == null ? 'sin estimación' : `USD ${Number(value).toFixed(4)}`;
  function selectedBody() {
    return {...preparation.selection(), reasoning_effort: $('adapt-effort').value || null,
      service_tier: $('adapt-service-tier').value, send_draft: $('adapt-send-draft').checked};
  }
  function currentQuote() { return costQuote && quoteIdentity === JSON.stringify(selectedBody()); }
  const statusLabels = {queued: "En espera", running: "En curso", completed: "Completado", partial: "Requiere revisión", unknown: "Resultado por comprobar", ready: "Adaptación guardada", draft: "Borrador en WordPress", stopped: "Detenido", failed: "Falló"};
  const node = (tag, text) => {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    return el;
  };

  function message(id, text) {
    $(id).textContent = text || "";
    $(id).hidden = !text;
  }

  function sync() {
    const selection = preparation.selection();
    const count = selection.article_ids.length * selection.configuration_ids.length;
    const available = catalog?.models?.some((m) => m.id === selection.model);
    const verified = capabilityModel === selection.model && capabilities;
    const running = activeRun && ["queued", "running"].includes(activeRun.status);
    let help = "";
    if (running) help = `Lote #${activeRun.id} en curso. Puede cerrar esta ventana y seguir el progreso en Preparación de adaptaciones.`;
    else if (!count) help = "Seleccione artículos y al menos una configuración.";
    else if (count > 500) help = "Reduzca la selección a un máximo de 500 combinaciones.";
    else if (!selection.model) help = "Elija un modelo de la cuenta para generar. La opción de usar el modelo de cada configuración sirve para preparar.";
    else if (!available) help = "El modelo no aparece en la lista disponible. Actualice los modelos.";
    else if (!verified) help = "Comprobando las opciones documentadas del modelo…";
    else if (!capabilities.automatic_supported) help = "Este modelo no admite todas las funciones necesarias para esta adaptación automática.";
    else if (!currentQuote()) help = 'Calcule y revise el coste antes de generar. La evaluación y la redacción son dos llamadas facturables.';
    else help = `Se evaluarán ${count} combinaciones, hasta ${count * 2} llamadas facturables. La API omitirá las que no correspondan al público.`;
    $("generation-start-help").textContent = help;
    const unavailable = submitting || preparation.busy() || running || !count || count > 500 || !available || !verified || !capabilities?.automatic_supported;
    $('preview-generation-cost').disabled = unavailable || calculatingCost;
    if (!currentQuote()) { $('accept-generation-cost').checked = false; $('accept-generation-cost').disabled = true; }
    $("start-generation").disabled = unavailable || !currentQuote() || !costQuote?.allowed || !$('accept-generation-cost').checked;
    $("adapt-effort").disabled = submitting || !verified || !capabilities?.reasoning_efforts?.length;
    $("adapt-send-draft").disabled = submitting;
  }

  async function previewCost() {
    calculatingCost = true;
    const body = selectedBody(), identity = JSON.stringify(body);
    $('accept-generation-cost').checked = false;
    sync();
    try {
      const quote = await panel.api('/api/generation/cost-preview', {method: 'POST', body: JSON.stringify(body)});
      if (identity !== JSON.stringify(selectedBody())) return;
      costQuote = quote; quoteIdentity = identity;
      const container = $('generation-cost-preview');
      const p = quote.projection, target = quote.target_projection;
      container.replaceChildren(
        node('p', `Este lote: ${usd(p.estimated_cost_usd_min)}–${usd(p.estimated_cost_usd_max)} · hasta ${quote.max_paid_requests} llamadas.`),
        node('p', `Meta: ${quote.budget.target_adaptations} adaptaciones, presupuesto ${usd(quote.budget.project_limit_usd)}. Consumo local estimado hasta ahora: ${usd(quote.spent_usd_max)}.`),
        node('p', `Proyección para ${quote.budget.target_adaptations} con este modelo y nivel: ${usd(target.estimated_cost_usd_min)}–${usd(target.estimated_cost_usd_max)}.`),
        node('p', quote.notice), ...quote.blockers.map((text) => node('p', text)));
      if (target.estimated_cost_usd_max > quote.budget.project_limit_usd) container.append(node('p', 'Este modelo no cumpliría la meta total con el consumo de la muestra. El modelo elegido no se cambia automáticamente.'));
      $('accept-generation-cost').disabled = !quote.allowed;
    } catch (error) { message('adapt-error', error.message); }
    finally { calculatingCost = false; sync(); }
  }

  async function loadUsage() {
    const data = await panel.api('/api/generation/usage');
    const container = $('generation-usage');
    const total = data.totals;
    container.replaceChildren(node('p', `${total.calls} llamadas registradas · ${usd(total.estimated_cost_usd_min)}–${usd(total.estimated_cost_usd_max)} estimados.`), node('p', data.scope));
    const trialTable = node('table'), trialHead = node('tr'), trialBody = node('tbody');
    ['Prueba', 'Modelo', 'Solicitudes', 'Total de tokens', 'Coste total estimado · USD'].forEach((label) => trialHead.append(node('th', label)));
    const trialHeader = node('thead'); trialHeader.append(trialHead); trialTable.append(trialHeader);
    (data.trials || []).forEach((trial) => {
      const row = node('tr'), t = trial.totals;
      const amount = (value) => value == null ? 'No calculable' : Number(value).toFixed(6);
      const cost = t.estimated_cost_usd_min === t.estimated_cost_usd_max ? amount(t.estimated_cost_usd_min)
        : `${amount(t.estimated_cost_usd_min)}–${amount(t.estimated_cost_usd_max)}`;
      [`#${trial.trial_id}${trial.deleted_locally ? ' · contenido borrado' : ''}`, trial.model, t.calls,
        `${t.usage_fields.tokensTotales}${trial.missing_assessment ? ' (parcial)' : ''}`, cost].forEach((value) => row.append(node('td', String(value))));
      trialBody.append(row);
    });
    trialTable.append(trialBody);
    const trialScroll = node('div'); trialScroll.className = 'table-scroll'; trialScroll.append(trialTable);
    container.append(node('h3', 'Total por prueba · evaluación y redacción'), trialScroll, node('h3', 'Detalle de cada llamada'));
    const table = node('table'), head = node('tr');
    ['Fecha · Bogotá', 'Etapa', 'Modelo / servicio', 'Entrada', 'Caché leída', 'Caché escrita', 'Salida total', 'Razonamiento', 'Total tokens', 'Coste estimado'].forEach((label) => head.append(node('th', label)));
    const thead = node('thead'); thead.append(head); table.append(thead);
    const tbody = node('tbody');
    data.records.forEach((record) => {
      const u = record.usage_fields, tr = node('tr');
      [new Intl.DateTimeFormat('es-CO', {dateStyle: 'short', timeStyle: 'short', timeZone: 'America/Bogota'}).format(new Date(record.created_at)), `${record.stage === 'assessment' ? 'Evaluación' : 'Redacción'} #${record.id}${record.deleted_locally ? ' · contenido borrado' : ''}`,
        `${record.model} / ${record.service_tier || 'desconocido'}${record.service_tier_confirmed ? '' : ' (solicitado)'}`,
        u.entrada, u.entradaCache, u.escrituraCache, u.salidaTotal, u.razonamiento, u.tokensTotales,
        `${usd(record.cost.estimated_cost_usd_min)}–${usd(record.cost.estimated_cost_usd_max)}`].forEach((value) => tr.append(node('td', value ?? 'Sin dato')));
      tbody.append(tr);
    });
    table.append(tbody);
    const scroll = node('div'); scroll.className = 'table-scroll'; scroll.append(table); container.append(scroll);
    container.append(node('p', 'El razonamiento ya está incluido en la salida total. La caché escrita forma parte de la entrada. El cálculo incluye búsquedas; el importe definitivo corresponde a la facturación de OpenAI.'));
  }

  async function selectCapabilities(force = false) {
    const model = preparation.selection().model;
    if (model === capabilityModel && !force) { sync(); return; }
    capabilityModel = model;
    capabilities = null;
    const sequence = ++capabilitySequence;
    $("adapt-effort").replaceChildren(new Option("Predeterminado del modelo", ""));
    $("adapt-effort-help").textContent = model ? "Consultando documentación oficial, sin generar contenido…" : "Seleccione un modelo para consultar sus esfuerzos disponibles.";
    sync();
    if (!model || model === "custom") return;
    try {
      const info = await panel.api(`/api/generation/models/${encodeURIComponent(model)}`);
      if (sequence !== capabilitySequence) return;
      capabilities = info;
      (info.reasoning_efforts || []).forEach((effort) => $("adapt-effort").append(new Option(effort, effort)));
      $("adapt-effort-help").textContent = info.warning || (info.reasoning_supported === false ? "Este modelo no permite configurar razonamiento." : `Opciones según la documentación oficial${info.default_reasoning_effort ? `; predeterminado: ${info.default_reasoning_effort}` : ""}.`);
    } catch (error) {
      if (sequence === capabilitySequence) $("adapt-effort-help").textContent = error.message;
    }
    sync();
  }

  async function loadModels(refresh = false) {
    if (loading) return loading;
    if (catalog && !refresh) { selectCapabilities(); return catalog; }
    $("adapt-refresh-models").disabled = true;
    $("adapt-model-reference").textContent = "Consultando modelos disponibles de la cuenta…";
    loading = (async () => {
      try {
        const data = await panel.api(refresh ? "/api/generation/models/refresh" : "/api/generation/models", refresh ? {method: "POST", body: "{}"} : {});
        catalog = data;
        const chosen = $("adapt-model").value;
        $("adapt-model").replaceChildren(new Option("Usar el modelo de cada configuración · solo preparación", ""));
        (data.models || []).forEach((model) => $("adapt-model").append(new Option(model.label || model.id, model.id)));
        if (data.models?.some((model) => model.id === chosen)) $("adapt-model").value = chosen;
        $("gpt-model-options").replaceChildren(...(data.models || []).map((model) => new Option(model.label || model.id, model.id)));
        $("adapt-model-reference").textContent = `${data.message || "Modelos consultados sin generar contenido."}${data.checked_at ? ` Consulta: ${panel.capturedDate(data.checked_at)}.` : ""}`;
        await selectCapabilities(true);
        return data;
      } catch (error) { $("adapt-model-reference").textContent = error.message; }
      finally { $("adapt-refresh-models").disabled = false; loading = null; sync(); }
    })();
    return loading;
  }

  function renderRun(run, container) {
    const section = node("section");
    section.className = "history-entry";
    section.append(node("h3", `Lote #${run.id} · ${statusLabels[run.status] || run.status}`));
    section.append(node("p", `${run.finished_count}/${run.total} combinaciones finalizadas · ${run.options.model} · Esfuerzo: ${run.options.reasoning_effort || "predeterminado"}`));
    const list = node("ul");
    run.items.forEach((item) => {
      const result = item.result || {};
      const label = result.decision === "omit" ? "Omitido por la API: tema no aplicable" : result.decision === "needs_verification" ? "Pendiente de verificación" : statusLabels[item.status] || item.status;
      const line = node("li", `Artículo #${item.article_id} · Configuración #${item.configuration_id}: ${label}${result.trial_id ? ` · Adaptación #${result.trial_id}` : ""}${item.reason ? ` — ${item.reason}` : ""}`);
      if (result.normalization?.status === "normalized") line.append(node("p", "Formato HTML ajustado automáticamente; se conservan el texto y la salida original de la API."));
      list.append(line);
    });
    section.append(list);
    if (run.can_resume_saved) {
      const resume = node("button", "Continuar con contenido guardado");
      resume.type = "button";
      resume.disabled = submitting;
      resume.addEventListener("click", () => resumeSaved(run.id, resume));
      section.append(resume, node("p", "Corrige el formato recuperable y continúa con la opción de WordPress elegida en este lote. No vuelve a evaluar ni a generar el artículo con OpenAI."));
    }
    container.append(section);
  }

  async function resumeSaved(runId, button) {
    if (submitting) return;
    submitting = true;
    button.disabled = true;
    sync();
    try {
      activeRun = await panel.api(`/api/generation/runs/${runId}/resume`, {method: "POST", body: "{}"});
      panel.notice(`Recuperando el lote #${runId} con su contenido guardado, sin una nueva generación.`);
      await loadRuns();
      if (activeRun) schedulePoll();
      else {
        await panel.refreshArticles();
        panel.notice(`Recuperación del lote #${runId} finalizada. Consulte el resultado actualizado del lote.`);
      }
    } catch (error) { panel.showError(error.message); }
    finally { submitting = false; button.disabled = false; sync(); }
  }

  async function loadRuns() {
    const data = await panel.api("/api/generation/runs");
    const container = $("generation-run-list");
    container.replaceChildren();
    data.items.forEach((run) => renderRun(run, container));
    if (!data.items.length) container.append(node("p", "Todavía no hay generaciones iniciadas desde el panel."));
    activeRun = data.items.find((run) => ["queued", "running"].includes(run.status)) || null;
    if (activeRun) schedulePoll();
    loadUsage().catch((error) => { $('generation-usage').textContent = error.message; });
    sync();
  }

  function schedulePoll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(pollRun, 3000);
  }

  async function pollRun() {
    if (!activeRun) return;
    try {
      const run = await panel.api(`/api/generation/runs/${activeRun.id}`);
      activeRun = run;
      $("adapt-generation-progress").replaceChildren();
      renderRun(run, $("adapt-generation-progress"));
      if (["queued", "running"].includes(run.status)) schedulePoll();
      else {
        activeRun = null;
        pendingRequest = null;
        await Promise.all([loadRuns(), panel.refreshArticles()]);
        panel.notice(`Lote #${run.id}: ${statusLabels[run.status] || run.status}. Consulte las adaptaciones en Revisión de datos.`);
      }
    } catch (error) {
      message("adapt-error", error.message);
      schedulePoll();
    }
    sync();
  }

  async function startGeneration() {
    if ($("start-generation").disabled || submitting) return;
    const body = {...selectedBody(), budget_quote_id: costQuote.quote_id};
    const identity = JSON.stringify(body);
    if (!pendingRequest || pendingRequest.identity !== identity) pendingRequest = {identity, key: crypto.randomUUID()};
    body.request_key = pendingRequest.key;
    submitting = true;
    preparation.setBusy(true);
    message("adapt-error", "");
    message("adapt-notice", "Validando la selección e iniciando la prueba…");
    try {
      activeRun = await panel.api("/api/generation/runs", {method: "POST", body: JSON.stringify(body)});
      $("adapt-generation-progress").replaceChildren();
      renderRun(activeRun, $("adapt-generation-progress"));
      message("adapt-notice", "Solicitud registrada. La evaluación y la redacción las realiza la API. Puede seguir el progreso en Preparación de adaptaciones.");
      schedulePoll();
    } catch (error) {
      message("adapt-error", error.message);
      message("adapt-notice", "");
    } finally { submitting = false; preparation.setBusy(false); sync(); }
  }

  window.addEventListener("generation:catalog", () => { loadModels(); });
  window.addEventListener("generation:selection", () => { selectCapabilities(); });
  window.addEventListener("panel:section", (event) => {
    if (event.detail.name === "generation") loadRuns().catch((error) => message("generation-error", error.message));
  });
  $("adapt-refresh-models").addEventListener("click", () => loadModels(true));
  $("adapt-model").addEventListener("change", () => selectCapabilities());
  $("adapt-effort").addEventListener("change", sync);
  $('adapt-service-tier').addEventListener('change', sync);
  $('adapt-send-draft').addEventListener('change', sync);
  $('accept-generation-cost').addEventListener('change', sync);
  $('preview-generation-cost').addEventListener('click', previewCost);
  $("start-generation").addEventListener("click", startGeneration);
  $("generation-open-selection").addEventListener("click", preparation.openDialog);
  $("generation-refresh-runs").addEventListener("click", () => loadRuns().catch((error) => message("generation-error", error.message)));
})();
