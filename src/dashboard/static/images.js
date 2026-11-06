"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const panel = window.ETPanel;
  const host = $("deployment-panel");
  if (!panel || !host || $("image-search-panel")) return;
  const state = { busy: false, candidates: [], notices: [], cards: [] };

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  const section = node("section", undefined, "card image-search-panel");
  section.id = "image-search-panel";
  section.setAttribute("aria-labelledby", "image-search-title");
  // Fixed UI markup only; catalogue values are always added with textContent.
  section.innerHTML = `
    <h2 id="image-search-title">Buscar una portada CC0</h2>
    <p>Busque fotografías externas y revise cuál representa el artículo. Al importar, la fotografía se guarda en los medios del destino seleccionado arriba.</p>
    <form id="image-search-form">
      <fieldset id="image-search-controls">
        <div class="image-search-fields">
          <label>Catálogo<select id="image-provider"><option value="wordpress_photos">WordPress Photos · CC0</option><option value="openverse">Openverse · solo CC0</option></select></label>
          <label>Qué debe mostrar la imagen<input id="image-query" type="search" required maxlength="150" placeholder="Por ejemplo: Barcelona" autocomplete="off"></label>
        </div>
        <div class="actions"><button id="image-search-submit" type="submit">Buscar fotografías</button><button id="image-suggest" type="button">Sugerir desde el artículo abierto</button></div>
      </fieldset>
    </form>
    <p id="image-source-article" class="muted hint" hidden></p>
    <div id="image-query-suggestions" class="actions" aria-label="Búsquedas sugeridas"></div>
    <p id="image-search-progress" role="status" hidden></p>
    <div id="image-search-error" class="notice error" role="alert" hidden></div>
    <div id="image-search-notice" class="notice success" role="status" hidden></div>
    <div id="image-search-warnings" class="muted hint" hidden></div>
    <p id="image-target-notice" class="muted hint">Seleccione el destino de WordPress antes de importar.</p>
    <div id="image-candidates" class="image-candidates"></div>`;
  host.insertBefore(section, host.querySelector(".deployment-image-note") || host.querySelector(".deployment-preview-card"));

  function message(id, text) {
    $(id).textContent = text || "";
    $(id).hidden = !text;
  }

  function currentTarget() {
    const picker = $("deployment-picker");
    const value = Number(picker.value);
    if (!Number.isSafeInteger(value) || value < 1 || picker.disabled) return null;
    return { id: value, label: picker.selectedOptions[0]?.textContent || `Destino ${value}` };
  }

  function sync() {
    $("image-search-controls").disabled = state.busy;
    const target = currentTarget();
    $("image-target-notice").textContent = target
      ? `Destino de la importación: ${target.label}.` : "Seleccione el destino de WordPress antes de importar.";
    state.cards.forEach(({ button, review, missingImage, blocked }) => {
      review.disabled = state.busy || missingImage;
      button.disabled = state.busy || !target || !review.checked || missingImage || blocked;
    });
    $("image-query-suggestions").querySelectorAll("button").forEach((button) => { button.disabled = state.busy; });
  }

  function setBusy(value, text = "") {
    state.busy = value;
    section.setAttribute("aria-busy", String(value));
    message("image-search-progress", value ? text : "");
    sync();
  }

  function safeUrl(value, image = false) {
    try {
      const url = new URL(value);
      if (url.protocol !== "https:" || url.username || url.password || (url.port && url.port !== "443")) return null;
      if (image && !(url.hostname === "pd.w.org" || url.hostname === "upload.wikimedia.org" || url.hostname.endsWith(".staticflickr.com"))) return null;
      return url.href;
    } catch { return null; }
  }

  function link(label, url) {
    const safe = safeUrl(url);
    if (!safe) return node("span", label);
    const element = node("a", label);
    element.href = safe;
    element.target = "_blank";
    element.rel = "noopener noreferrer";
    return element;
  }

  function fillMedia(record, target) {
    const current = currentTarget();
    if (!current || current.id !== target.id) {
      message("image-search-notice", `Imagen ${record.wp_media_id} disponible en ${target.label}. El destino cambió; no se rellenó el campo de la portada actual.`);
      return;
    }
    const input = $("deployment-featured-media");
    input.value = String(record.wp_media_id);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    message("image-search-notice", `Imagen disponible en ${target.label}: ID ${record.wp_media_id}. Se rellenó el campo de imagen destacada para preparar el borrador.`);
  }

  async function importImage(candidate, card) {
    const target = currentTarget();
    if (state.busy || !target || !card.review.checked) return;
    const retry = card.failed === true;
    const action = retry ? "¿Reintentar la importación fallida después de corregir su causa?" : "¿Importar esta fotografía?";
    const confirmation = `${action}\nDestino: ${target.label}\n\n${candidate.title}\nAutor: ${candidate.creator || "Sin dato"}\nLicencia: CC0\n\nEsta acción sube un archivo al sitio. No crea ni publica artículos. Si ya se importó, se reutilizará su ID.`;
    if (!window.confirm(confirmation)) return;
    setBusy(true, "Verificando la fotografía e importándola a WordPress…");
    message("image-search-error", "");
    message("image-search-notice", "");
    try {
      const record = await panel.api("/api/deployment/images/import", {
        method: "POST", body: JSON.stringify({ target_id: target.id, candidate_id: candidate.id, confirm: true,
          ...(retry ? { retry_failed: true } : {}) }),
      });
      card.failed = record.status === "failed";
      card.blocked = record.status === "unknown" || record.status === "importing";
      card.button.textContent = card.failed ? "Reintentar importación tras corregir el error" : "Importar al destino seleccionado";
      if (record.status === "ready") {
        fillMedia(record, target);
        card.status.textContent = `Disponible en ${target.label} · ID ${record.wp_media_id}`;
      } else {
        const detail = record.error || "La importación sigue reservada; compruebe el resultado en WordPress antes de cualquier nueva operación.";
        card.status.textContent = detail;
        message("image-search-error", detail);
      }
    } catch (error) {
      // A browser/network failure can follow a completed upload. Read the journal
      // rather than issuing another POST automatically.
      try {
        const query = new URLSearchParams({ target_id: target.id, candidate_id: candidate.id });
        const result = await panel.api(`/api/deployment/images/import?${query}`);
        card.failed = result.record?.status === "failed";
        card.blocked = result.record?.status === "unknown" || result.record?.status === "importing";
        card.button.textContent = card.failed ? "Reintentar importación tras corregir el error" : "Importar al destino seleccionado";
        if (result.record?.status === "ready") {
          fillMedia(result.record, target);
          card.status.textContent = `Disponible en ${target.label} · ID ${result.record.wp_media_id}`;
        } else {
          const detail = result.record?.error || (result.record
            ? "La importación quedó reservada. Compruebe WordPress; no se reenvió el archivo." : error.message);
          card.status.textContent = detail;
          message("image-search-error", detail);
        }
      } catch { message("image-search-error", "No se pudo confirmar la importación. Compruebe WordPress antes de continuar; no se reenvió el archivo."); }
    } finally { setBusy(false); }
  }

  function render(result) {
    state.candidates = result.candidates || [];
    state.cards = [];
    const list = $("image-candidates");
    list.replaceChildren();
    const warnings = node("ul");
    (result.warnings || []).forEach((value) => warnings.append(node("li", value)));
    $("image-search-warnings").replaceChildren(warnings);
    $("image-search-warnings").hidden = !(result.warnings || []).length;
    if (!state.candidates.length) {
      list.append(node("p", "No hay fotografías aptas para esta búsqueda. Cambie las palabras o pruebe el otro catálogo."));
      return;
    }
    state.candidates.forEach((candidate) => {
      const card = node("article", undefined, "image-candidate");
      const picture = node("img");
      const imageUrl = safeUrl(candidate.image_url, true);
      picture.alt = candidate.title;
      picture.loading = "lazy";
      picture.decoding = "async";
      picture.referrerPolicy = "no-referrer";
      if (imageUrl) picture.src = imageUrl;
      const body = node("div", undefined, "image-candidate-body");
      body.append(node("h3", candidate.title));
      body.append(node("p", `${candidate.width} × ${candidate.height} · ${candidate.creator || "Autor no indicado"}`, "muted hint"));
      if (candidate.id === result.selected_candidate_id) body.append(node("p", "Primera sugerencia por palabras y formato; requiere su revisión.", "muted hint"));
      const links = node("p", undefined, "image-candidate-links");
      links.append(link("Ver fuente original", candidate.source_url), document.createTextNode(" · "), link("Licencia CC0", candidate.license_url));
      const label = node("label", undefined, "check-label");
      const review = node("input");
      review.type = "checkbox";
      label.append(review, document.createTextNode("Revisé la imagen y representa el artículo"));
      const button = node("button", "Importar al destino seleccionado");
      button.type = "button";
      const status = node("p", "", "muted hint");
      status.setAttribute("role", "status");
      const controls = { button, review, status, missingImage: !imageUrl };
      picture.addEventListener("error", () => {
        controls.missingImage = true;
        review.checked = false;
        status.textContent = "No se pudo cargar la fotografía para revisarla. Pruebe otra candidata.";
        sync();
      });
      review.addEventListener("change", sync);
      button.addEventListener("click", () => importImage(candidate, controls));
      body.append(links, label, button, status);
      card.append(picture, body);
      list.append(card);
      state.cards.push(controls);
    });
    sync();
  }

  async function search(event) {
    event.preventDefault();
    if (state.busy || !$("image-search-form").reportValidity()) return;
    const query = $("image-query").value.trim();
    if (!query) return;
    setBusy(true, "Consultando el catálogo externo…");
    message("image-search-error", "");
    message("image-search-notice", "");
    $("image-candidates").replaceChildren();
    $("image-search-warnings").hidden = true;
    state.cards = [];
    try {
      render(await panel.api("/api/deployment/images/search", {
        method: "POST", body: JSON.stringify({ query, provider: $("image-provider").value }),
      }));
    } catch (error) { message("image-search-error", error.message); }
    finally { setBusy(false); }
  }

  async function suggest() {
    if (state.busy) return;
    setBusy(true, "Consultando el título del artículo…");
    message("image-search-error", "");
    try {
      let id = panel.currentArticleId?.();
      if (!id) {
        const ids = await panel.selectedIds();
        if (ids.length === 1) id = ids[0];
      }
      if (!id) throw new Error("Abra un artículo en Revisión de datos o seleccione uno solo para sugerir una búsqueda.");
      const result = await panel.api(`/api/deployment/images/suggestions?article_id=${encodeURIComponent(id)}`);
      message("image-source-article", `Artículo de referencia: ${result.article_title}`);
      const suggestions = $("image-query-suggestions");
      suggestions.replaceChildren();
      (result.queries || []).forEach((query) => {
        const button = node("button", query);
        button.type = "button";
        button.addEventListener("click", () => { $("image-query").value = query; $("image-query").focus(); });
        suggestions.append(button);
      });
      if (result.queries?.length) $("image-query").value = result.queries[0];
      else message("image-search-notice", "No hay una sugerencia clara para este título. Escriba la ciudad o un objeto concreto relacionado con el contenido.");
    } catch (error) { message("image-search-error", error.message); }
    finally { setBusy(false); }
  }

  $("image-search-form").addEventListener("submit", search);
  $("image-suggest").addEventListener("click", suggest);
  $("deployment-picker").addEventListener("change", () => {
    const input = $("deployment-featured-media");
    input.value = "";
    input.dispatchEvent(new Event("input", { bubbles: true }));
    message("image-search-notice", "");
    state.cards.forEach((card) => {
      card.failed = false;
      card.blocked = false;
      card.status.textContent = "";
      card.button.textContent = "Importar al destino seleccionado";
    });
    sync();
  });
  new MutationObserver(sync).observe($("deployment-picker"), { childList: true, attributes: true, attributeFilter: ["disabled"] });
  sync();
})();
