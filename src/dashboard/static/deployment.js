"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const panel = window.ETPanel;
  const state = { catalog: null, targetId: null, loading: null, busy: false, dirty: false };
  const markers = {
    "en-CA": [["CTA_IMMIGRATION_EN", "Inmigración"], ["CTA_TAXES_EN", "Impuestos"]],
    "fr-CA": [["CTA_IMMIGRATION_FR", "Inmigración"], ["CTA_TAXES_FR", "Impuestos"]],
    "es-CO": [["CTA_INMIGRACION_ES", "Inmigración"], ["CTA_IMPUESTOS_ES", "Impuestos"]],
  };

  function node(tag, value, className) {
    const element = document.createElement(tag);
    if (value !== undefined) element.textContent = String(value);
    if (className) element.className = className;
    return element;
  }

  function message(id, text) {
    $(id).textContent = text || "";
    $(id).hidden = !text;
  }

  function messages(id, values) {
    const items = [...new Set((values || []).map((value) => typeof value === "string" ? value : value.message || JSON.stringify(value)))];
    const list = node("ul");
    items.forEach((value) => list.append(node("li", value)));
    $(id).replaceChildren(...(items.length ? [list] : []));
    $(id).hidden = !items.length;
  }

  function target() {
    return state.catalog?.targets?.find((item) => String(item.id) === String(state.targetId));
  }

  function syncControls() {
    const unavailable = state.busy || !target();
    $("deployment-picker").disabled = unavailable;
    $("deployment-refresh").disabled = state.busy;
    $("deployment-target-controls").disabled = unavailable;
    $("deployment-preview-controls").disabled = unavailable;
    $("deployment-preview-submit").disabled = unavailable || state.dirty;
    $("deployment-dirty").hidden = !state.dirty;
    $("deployment-send").disabled = true;
  }

  function clearPreview() {
    $("deployment-preview-result").hidden = true;
    $("deployment-preview-payload").textContent = "";
  }

  function ctaFields(item) {
    const fragment = document.createDocumentFragment();
    const known = markers[item.locale] || [];
    known.forEach(([marker, description]) => {
      const fieldset = node("fieldset", undefined, "deployment-cta");
      fieldset.dataset.cta = marker;
      const legend = node("legend", `${description} · [${marker}]`);
      const grid = node("div", undefined, "form-grid");
      const urlLabel = node("label", "URL del enlace");
      const url = node("input");
      url.type = "url";
      url.maxLength = 2000;
      url.placeholder = "https://…";
      url.autocomplete = "off";
      url.dataset.ctaField = "url";
      url.value = item.cta_links?.[marker]?.url || "";
      const textLabel = node("label", "Texto visible del enlace");
      const text = node("input");
      text.type = "text";
      text.maxLength = 300;
      text.placeholder = "Por definir";
      text.dataset.ctaField = "text";
      text.value = item.cta_links?.[marker]?.text || "";
      urlLabel.append(url);
      textLabel.append(text);
      grid.append(urlLabel, textLabel);
      fieldset.append(legend, grid);
      fragment.append(fieldset);
    });
    $("deployment-cta-fields").replaceChildren(fragment);
  }

  function fillTarget(id) {
    state.targetId = id;
    state.dirty = false;
    clearPreview();
    const item = target();
    if (!item) { syncControls(); return; }
    $("deployment-picker").value = String(item.id);
    $("deployment-base-url").textContent = item.base_url;
    $("deployment-locale").textContent = item.locale;
    $("deployment-updated").textContent = item.updated_at ? panel.capturedDate(item.updated_at) : "Sin dato";
    const credentials = item.credentials || {};
    $("deployment-credentials").textContent = `Usuario: ${credentials.username_configured ? "configurado" : "pendiente"} · Contraseña de aplicación: ${credentials.application_password_configured ? "disponible" : "pendiente"} · Sin comprobar conexión`;
    $("deployment-guide-url").value = item.guide_url || "";
    $("deployment-categories").value = (item.category_ids || []).join(", ");
    $("deployment-language-mode").value = item.language_mode || "pending";
    $("deployment-language-mode").querySelector('option[value="site_default"]').disabled = item.locale === "fr-CA";
    $("deployment-notes").value = item.notes || "";
    $("deployment-language-help").textContent = item.locale === "fr-CA"
      ? "El destino francés todavía necesita su sección de guías y confirmar cómo se gestiona el idioma en WordPress. Una ruta en francés no configura por sí sola la traducción."
      : "Las categorías deben corresponder a las que utiliza el listado de guías. El idioma y la inclusión en ese listado se comprobarán con el sitio antes de enviar artículos.";
    messages("deployment-blockers", item.blockers || []);
    ctaFields(item);
    window.dispatchEvent(new CustomEvent('deployment:target', {detail: {targetId: item.id}}));
    syncControls();
  }

  async function loadCatalog(force = false) {
    if (state.loading) return state.loading;
    if (state.catalog && !force) return state.catalog;
    state.busy = true;
    syncControls();
    state.loading = (async () => {
      const data = await panel.api("/api/deployment/catalog");
      state.catalog = data;
      const fragment = document.createDocumentFragment();
      (data.targets || []).forEach((item) => {
        const option = node("option", `${item.name} · ${item.locale}`);
        option.value = item.id;
        fragment.append(option);
      });
      $("deployment-picker").replaceChildren(fragment);
      const exists = (data.targets || []).some((item) => String(item.id) === String(state.targetId));
      fillTarget(exists ? state.targetId : data.targets?.[0]?.id || null);
      if (!data.targets?.length) message("deployment-error", "No hay destinos de WordPress configurados.");
      return data;
    })();
    try { return await state.loading; }
    finally { state.loading = null; state.busy = false; syncControls(); }
  }

  function configurationPayload() {
    const raw = $("deployment-categories").value.trim();
    const tokens = raw ? raw.split(",").map((value) => value.trim()) : [];
    if (tokens.some((value) => !/^[1-9]\d*$/.test(value) || !Number.isSafeInteger(Number(value)))) {
      throw new Error("Use IDs de categoría enteros positivos, separados por comas.");
    }
    const ctaLinks = {};
    $("deployment-cta-fields").querySelectorAll("[data-cta]").forEach((fieldset) => {
      ctaLinks[fieldset.dataset.cta] = {
        url: fieldset.querySelector('[data-cta-field="url"]').value.trim(),
        text: fieldset.querySelector('[data-cta-field="text"]').value.trim(),
      };
    });
    return {
      guide_url: $("deployment-guide-url").value.trim(),
      category_ids: [...new Set(tokens.map(Number))],
      language_mode: $("deployment-language-mode").value,
      cta_links: ctaLinks,
      notes: $("deployment-notes").value,
    };
  }

  async function saveConfiguration(event) {
    event.preventDefault();
    if (state.busy || !target()) return;
    message("deployment-error", "");
    message("deployment-notice", "");
    let payload;
    try { payload = configurationPayload(); }
    catch (error) { message("deployment-error", error.message); return; }
    state.busy = true;
    syncControls();
    try {
      await panel.api(`/api/deployment/targets/${encodeURIComponent(state.targetId)}`, { method: "PUT", body: JSON.stringify(payload) });
      await loadCatalog(true);
      window.dispatchEvent(new CustomEvent("panel:ctas-updated", { detail: { destinationId: target()?.destination_id, source: "deployment" } }));
      message("deployment-notice", "Configuración guardada localmente. No se ha enviado nada a WordPress.");
    } catch (error) { message("deployment-error", error.message); }
    finally { state.busy = false; syncControls(); }
  }

  async function preview(event) {
    event.preventDefault();
    if (state.busy || !target() || state.dirty) return;
    state.busy = true;
    syncControls();
    clearPreview();
    message("deployment-error", "");
    const media = $("deployment-featured-media").value;
    try {
      const result = await panel.api("/api/deployment/preview", {
        method: "POST", body: JSON.stringify({
          target_id: Number(state.targetId), title: $("deployment-preview-title").value.trim(),
          html: $("deployment-preview-html").value, excerpt: $("deployment-preview-excerpt").value.trim(),
          featured_media: media ? Number(media) : null,
        }),
      });
      $("deployment-preview-status").textContent = result.ready ? "Estructura local válida · envío deshabilitado" : "Borrador con puntos pendientes";
      messages("deployment-preview-errors", result.errors || []);
      messages("deployment-preview-warnings", result.warnings || []);
      $("deployment-payload-wrap").hidden = !result.payload;
      $("deployment-preview-payload").textContent = result.payload ? JSON.stringify(result.payload, null, 2) : "";
      $("deployment-preview-result").hidden = false;
    } catch (error) { message("deployment-error", error.message); }
    finally { state.busy = false; syncControls(); }
  }

  window.addEventListener("panel:section", (event) => {
    const active = event.detail.name === "deployment";
    $("deployment-panel").hidden = !active;
    if (active) loadCatalog().catch((error) => message("deployment-error", error.message));
  });
  window.addEventListener("panel:ctas-updated", (event) => {
    if (event.detail.source !== "deployment") state.catalog = null;
  });
  $("deployment-picker").addEventListener("change", () => {
    message("deployment-error", "");
    message("deployment-notice", "");
    fillTarget($("deployment-picker").value);
  });
  $("deployment-target-form").addEventListener("input", () => {
    state.dirty = true;
    clearPreview();
    syncControls();
  });
  $("deployment-preview-form").addEventListener("input", clearPreview);
  $("deployment-target-form").addEventListener("submit", saveConfiguration);
  $("deployment-preview-form").addEventListener("submit", preview);
  $("deployment-refresh").addEventListener("click", () => {
    message("deployment-error", "");
    message("deployment-notice", "");
    loadCatalog(true).catch((error) => message("deployment-error", error.message));
  });
})();
