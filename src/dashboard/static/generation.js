"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const panel = window.ETPanel;
  const state = {
    catalog: null, loading: null, articleIds: [], dialogBusy: false,
    destinationId: null, promptId: null, configurationId: null,
    saving: false, dialogSequence: 0,
  };

  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined && text !== null) element.textContent = String(text);
    if (className) element.className = className;
    return element;
  }

  function message(id, value) {
    $(id).textContent = value || "";
    $(id).hidden = !value;
  }

  function warningList(container, warnings) {
    const unique = [...new Set((warnings || []).map((warning) => typeof warning === "string" ? warning : warning.message || JSON.stringify(warning)))];
    const list = node("ul");
    unique.forEach((warning) => list.append(node("li", warning)));
    container.replaceChildren(...(unique.length ? [list] : []));
    container.hidden = !unique.length;
  }

  function options(select, items, emptyLabel, selectedId) {
    const fragment = document.createDocumentFragment();
    if (emptyLabel) {
      const empty = node("option", emptyLabel);
      empty.value = "";
      fragment.append(empty);
    }
    items.forEach((item) => {
      const option = node("option", item.label);
      option.value = String(item.id);
      option.disabled = Boolean(item.disabled);
      fragment.append(option);
    });
    select.replaceChildren(fragment);
    select.value = selectedId === undefined || selectedId === null ? "" : String(selectedId);
  }

  function find(collection, id) {
    return (state.catalog?.[collection] || []).find((item) => String(item.id) === String(id));
  }

  function promptVersions() {
    const result = [];
    (state.catalog?.prompts || []).forEach((prompt) => {
      const versions = prompt.versions?.length ? prompt.versions : [{ id: prompt.current_version_id, version: prompt.current_version, status: prompt.status }];
      versions.forEach((version) => {
        const id = version.id || version.version_id;
        const versionNumber = version.version || version.version_number;
        result.push({
          id, disabled: Boolean(prompt.archived),
          label: `${prompt.name} · v${versionNumber || "—"}${String(id) === String(prompt.current_version_id) ? " · actual" : ""}${version.status === "reviewed" ? " · revisado" : " · borrador"}${prompt.archived ? " · archivado" : ""}`,
        });
      });
    });
    return result;
  }

  function fillDestination(id) {
    state.destinationId = id || null;
    const item = find("destinations", id);
    $("destination-picker").value = item ? String(item.id) : "";
    $("destination-name").value = item?.name || "";
    $("destination-url").value = item?.site_url || "";
    $("destination-locale").value = item?.locale || "";
    $("destination-notes").value = item?.notes || "";
    $("destination-enabled").checked = item ? Boolean(item.enabled) : true;
  }

  function fillPrompt(id) {
    state.promptId = id || null;
    const item = find("prompts", id);
    $("prompt-picker").value = item ? String(item.id) : "";
    $("prompt-name").value = item?.name || "";
    $("prompt-description").value = item?.description || "";
    $("prompt-system").value = item?.system_prompt || "";
    $("prompt-template").value = item?.template || "";
    $("prompt-status").value = item?.status || "draft";
    $("prompt-archived").checked = Boolean(item?.archived);
    $("prompt-version").textContent = item ? `Editando la versión ${item.current_version}. Los cambios se guardan en una nueva versión.` : "Nuevo prompt · Puede guardarlo vacío como borrador.";
    const versions = item?.versions || [];
    $("prompt-history").replaceChildren(...(versions.length ? versions.map((version) => node("p", `Versión ${version.version || version.version_number} · ${version.status === "reviewed" ? "Revisado" : "Borrador"} · ${panel.capturedDate(version.created_at)}`)) : [node("p", "Todavía no hay versiones guardadas.", "muted")]));
  }

  function fillConfiguration(id) {
    state.configurationId = id || null;
    const item = find("configurations", id);
    $("configuration-picker").value = item ? String(item.id) : "";
    $("configuration-name").value = item?.name || "";
    options($("configuration-destination"), (state.catalog?.destinations || []).map((destination) => ({
      id: destination.id, label: `${destination.name} · ${destination.locale}${destination.enabled ? "" : " · deshabilitado"}`,
      disabled: !destination.enabled,
    })), "Seleccione un destino", item?.destination_id);
    options($("configuration-prompt"), promptVersions(), "Seleccione un prompt y su versión", item?.prompt_version_id);
    $("configuration-provider").value = item?.provider || "openai";
    $("configuration-model").value = item?.model || "";
    $("configuration-notes").value = item?.notes || "";
    $("configuration-enabled").checked = item ? Boolean(item.enabled) : true;
    $("configuration-version").textContent = item ? `Versión ${item.current_version} · ${item.prompt_name} v${item.prompt_version}${item.newer_prompt_available ? " · Hay una versión más reciente del prompt; selecciónela explícitamente si quiere usarla." : ""}` : "Nueva configuración · El modelo puede quedar pendiente.";
    warningList($("configuration-warnings"), item?.warnings || []);
    $("configuration-prompt-detail").hidden = !item;
    $("configuration-prompt-detail").open = false;
    delete $("configuration-prompt-detail").dataset.loaded;
    $("configuration-prompt-label").textContent = "";
    $("configuration-prompt-text").textContent = "";
  }

  function renderCatalog({ chooseDefaults = false } = {}) {
    const catalog = state.catalog;
    const credentials = (catalog.credentials || []).map((credential) => `${credential.provider === "openai" ? "OpenAI" : "Gemini"}: ${credential.configured ? "clave local disponible (sin comprobar)" : "sin clave local"}`);
    $("generation-credentials").textContent = credentials.join(" · ");
    $("prompt-placeholders").textContent = (catalog.placeholders || []).map((value) => {
      const name = typeof value === "string" ? value : value.name || "";
      return name.startsWith("{{") ? name : `{{${name}}}`;
    }).join(", ");
    if (chooseDefaults) {
      state.destinationId = catalog.destinations[0]?.id || null;
      state.promptId = catalog.prompts[0]?.id || null;
      state.configurationId = catalog.configurations[0]?.id || null;
    }
    options($("destination-picker"), catalog.destinations.map((item) => ({ id: item.id, label: `${item.name} · ${item.locale}${item.enabled ? "" : " · deshabilitado"}` })), "Nuevo destino", state.destinationId);
    options($("prompt-picker"), catalog.prompts.map((item) => ({ id: item.id, label: `${item.name} · v${item.current_version}${item.archived ? " · archivado" : ""}` })), "Nuevo prompt", state.promptId);
    options($("configuration-picker"), catalog.configurations.map((item) => ({ id: item.id, label: `${item.name}${item.enabled ? "" : " · deshabilitada"}` })), "Nueva configuración", state.configurationId);
    fillDestination(state.destinationId);
    fillPrompt(state.promptId);
    fillConfiguration(state.configurationId);
    const reference = catalog.model_reference || {};
    const models = reference.models || [];
    options($("adapt-model"), [...models.map((model) => ({id: model.id, label: model.label})), {id: "custom", label: "Otro modelo GPT…"}], "Usar el modelo de cada configuración", "");
    $("gpt-model-options").replaceChildren(...models.map((model) => {
      const option = node("option", model.label);
      option.value = model.id;
      return option;
    }));
    $("adapt-model-reference").textContent = `Opciones de referencia revisadas el ${reference.checked_at || "—"}. La disponibilidad y las tarifas se comprobarán al activar las pruebas; elegir un modelo ahora no lo ejecuta.`;
  }

  async function loadCatalog({ force = false } = {}) {
    if (state.loading) return state.loading;
    if (state.catalog && !force) return state.catalog;
    state.loading = (async () => {
      const firstLoad = !state.catalog;
      const catalog = await panel.api("/api/generation/catalog");
      state.catalog = catalog;
      renderCatalog({ chooseDefaults: firstLoad });
      return catalog;
    })();
    try { return await state.loading; }
    finally { state.loading = null; }
  }

  function switchTab(name) {
    document.querySelectorAll("[data-generation-tab]").forEach((button) => {
      const active = button.dataset.generationTab === name;
      button.setAttribute("aria-selected", String(active));
      button.tabIndex = active ? 0 : -1;
      $(button.getAttribute("aria-controls")).hidden = !active;
    });
    message("generation-error", "");
    message("generation-notice", "");
    if (name === "plans") loadPlans().catch((error) => message("generation-error", error.message));
  }

  function setSaving(value) {
    state.saving = value;
    ["destination", "prompt", "configuration"].forEach((kind) => {
      $(`${kind}-controls`).disabled = value;
      $(`${kind}-picker`).disabled = value;
    });
  }

  async function save(kind, collection, payload) {
    if (state.saving) return;
    message("generation-error", "");
    message("generation-notice", "");
    const id = state[`${kind}Id`];
    setSaving(true);
    try {
      const saved = await panel.api(`/api/generation/${collection}${id ? `/${encodeURIComponent(id)}` : ""}`, {
        method: id ? "PUT" : "POST", body: JSON.stringify(payload),
      });
      state[`${kind}Id`] = saved.id || saved[kind]?.id || id;
      await loadCatalog({ force: true });
      message("generation-notice", kind === "prompt" ? "Prompt guardado. Las configuraciones conservan la versión que ya tenían seleccionada." : kind === "configuration" ? "Configuración guardada para preparar artículos. La generación sigue deshabilitada." : "Destino guardado. Las preparaciones anteriores conservan sus datos originales.");
    } catch (error) { message("generation-error", error.message); }
    finally { setSaving(false); }
  }

  function selectedConfigurationIds() {
    return [...$("adapt-configurations").querySelectorAll("input:checked")].map((input) => Number(input.value));
  }

  function selectedModel() {
    return $("adapt-model").value === "custom" ? $("adapt-custom-model").value.trim() : $("adapt-model").value;
  }

  function syncDialog() {
    const configurations = selectedConfigurationIds().length;
    const pairs = state.articleIds.length * configurations;
    const invalidModel = $("adapt-model").value === "custom" && !/^gpt-[A-Za-z0-9][A-Za-z0-9._:-]*$/.test(selectedModel());
    $("adapt-pair-count").textContent = `${state.articleIds.length} artículos × ${configurations} configuraciones = ${pairs} combinaciones${pairs > 500 ? " · Reduzca la selección a un máximo de 500." : ""}`;
    $("preview-preparation").disabled = state.dialogBusy || !pairs || pairs > 500 || invalidModel;
    $("save-preparation").disabled = state.dialogBusy || !pairs || pairs > 500 || invalidModel;
    $("adapt-model-controls").disabled = state.dialogBusy;
    $("adapt-custom-model-wrap").hidden = $("adapt-model").value !== "custom";
    $("close-adapt-dialog").disabled = state.dialogBusy;
    $("adapt-configurations").querySelectorAll("input").forEach((input) => {
      input.disabled = state.dialogBusy;
      const configuration = find("configurations", input.value);
      const chosen = !invalidModel && selectedModel() && configuration.provider === "openai" ? selectedModel() : configuration.model;
      const label = input.closest("label");
      label.querySelector(".choice-model").textContent = `${configuration.provider === "openai" ? "OpenAI" : "Gemini"} · Modelo del lote: ${chosen || "por definir"}`;
      warningList(label.querySelector(".choice-warnings"), (configuration.warnings || []).filter((warning) => !chosen || warning !== "Falta elegir un modelo."));
    });
    $("adapt-dialog").setAttribute("aria-busy", String(state.dialogBusy));
  }

  function renderChoices() {
    const configurations = state.catalog.configurations.filter((item) => {
      const destination = find("destinations", item.destination_id);
      const prompt = find("prompts", item.prompt_id) || state.catalog.prompts.find((candidate) => candidate.versions?.some((version) => String(version.id) === String(item.prompt_version_id)));
      return item.enabled && destination?.enabled && !prompt?.archived;
    });
    const fragment = document.createDocumentFragment();
    configurations.forEach((configuration) => {
      const label = node("label", null, "configuration-choice");
      const input = node("input");
      input.type = "checkbox";
      input.value = configuration.id;
      const description = node("span");
      description.append(node("strong", configuration.name));
      description.append(node("span", `${configuration.destination_name} · ${configuration.locale} · Configuración v${configuration.current_version} · ${configuration.prompt_name} v${configuration.prompt_version}`, "muted"));
      description.append(node("span", "", "muted choice-model"));
      const warnings = node("div", null, "choice-warnings");
      warningList(warnings, configuration.warnings);
      description.append(warnings);
      label.append(input, description);
      input.addEventListener("change", () => {
        $("preparation-preview").hidden = true;
        message("adapt-notice", "");
        message("adapt-error", "");
        syncDialog();
      });
      fragment.append(label);
    });
    if (!configurations.length) fragment.append(node("p", "No hay configuraciones disponibles. Cree o habilite un destino, un prompt y una configuración en Preparación de adaptaciones.", "muted"));
    $("adapt-configurations").replaceChildren(fragment);
  }

  async function openDialog() {
    if ($("adapt-dialog").open) return;
    const sequence = ++state.dialogSequence;
    state.articleIds = [];
    $("adapt-model").value = "";
    $("adapt-custom-model").value = "";
    state.dialogBusy = true;
    $("adapt-article-count").textContent = "Cargando la selección guardada…";
    $("adapt-configurations").replaceChildren();
    $("preparation-preview").hidden = true;
    message("adapt-error", "");
    message("adapt-notice", "");
    $("adapt-dialog").showModal();
    syncDialog();
    try {
      const [ids] = await Promise.all([panel.selectedIds(), loadCatalog({ force: true })]);
      if (sequence !== state.dialogSequence || !$("adapt-dialog").open) return;
      state.articleIds = ids;
      $("adapt-article-count").textContent = `${ids.length} artículos seleccionados. Se usará su versión limpia actual.`;
      renderChoices();
      if (!ids.length) message("adapt-error", "Seleccione al menos un artículo en Revisión de datos.");
    } catch (error) { message("adapt-error", error.message); }
    finally { state.dialogBusy = false; syncDialog(); }
  }

  function renderPreview(preview) {
    const example = preview.example || {};
    $("preview-summary").textContent = `${preview.pair_count} combinaciones comprobadas localmente. Se muestra el primer ejemplo: artículo #${example.article_id || "—"}, configuración #${example.configuration_id || "—"}. Modelo: ${example.generation_options?.model || "por definir"}. No es una adaptación generada.`;
    const warnings = [...(preview.warnings || []), ...(example.warnings || [])];
    warningList($("preview-warnings"), warnings);
    $("preview-system").textContent = example.system_prompt || "Sin instrucciones del sistema · pendiente de plantilla.";
    $("preview-template").textContent = example.rendered_prompt || "Plantilla vacía · pendiente de definición.";
    $("preview-source").textContent = JSON.stringify(example.source_context || {}, null, 2);
    $("preparation-preview").hidden = false;
  }

  async function prepare(action) {
    if (state.dialogBusy) return;
    const configurationIds = selectedConfigurationIds();
    if (!configurationIds.length || !state.articleIds.length) return;
    state.dialogBusy = true;
    syncDialog();
    message("adapt-error", "");
    message("adapt-notice", "");
    try {
      const result = await panel.api(`/api/generation/${action === "preview" ? "preview" : "plans"}`, {
        method: "POST", body: JSON.stringify({ article_ids: state.articleIds, configuration_ids: configurationIds, model_override: selectedModel() }),
      });
      if (action === "preview") {
        renderPreview(result);
        message("adapt-notice", "Vista previa calculada localmente. No se ha guardado una preparación ni generado contenido.");
      } else {
        $("preparation-preview").hidden = true;
        message("adapt-notice", `Preparación guardada con las versiones vigentes al guardar: ${result.created_count || 0} combinaciones nuevas y ${result.reused_count || 0} ya preparadas. Consulte sus versiones en Preparaciones guardadas. Todavía no hubo adaptaciones.`);
        await Promise.all([panel.refreshArticles(), loadPlans()]);
      }
    } catch (error) { message("adapt-error", error.message); }
    finally { state.dialogBusy = false; syncDialog(); }
  }

  function preparationItem(item) {
    const article = item.article_title || item.title || `Artículo #${item.article_id}`;
    const config = item.configuration_name || item.configuration_snapshot?.name || `Configuración #${item.configuration_id}`;
    const version = item.configuration_version || item.configuration_version_number || item.configuration_version_id || "—";
    const promptVersion = item.prompt_version || item.prompt_version_number || item.prompt_version_id || "—";
    const line = node("li");
    line.append(node("strong", article), node("span", `${config} · Configuración v${version} · Prompt v${promptVersion} · Origen #${item.source_version_id || item.article_version_id || "—"}`, "muted"));
    if (item.destination_name) line.append(node("span", `${item.destination_name}${item.locale ? ` · ${item.locale}` : ""}`, "muted"));
    line.append(node("span", `Modelo previsto: ${item.provider || "—"} · ${item.model || "por definir"}`, "muted"));
    const changes = [];
    if (item.source_changed) changes.push("El artículo tiene una versión más reciente.");
    if (item.configuration_changed) changes.push("La configuración ha cambiado.");
    if (item.destination_changed) changes.push("El destino ha cambiado.");
    if (item.newer_prompt_available) changes.push("Hay una versión más reciente del prompt; esta preparación conserva la anterior.");
    changes.forEach((change) => line.append(node("span", change, "review-mark")));
    return line;
  }

  async function loadPlanDetails(plan, details, container) {
    if (!details.open || details.dataset.loaded || details.dataset.loading) return;
    details.dataset.loading = "true";
    container.replaceChildren(node("p", "Cargando combinaciones…", "muted"));
    try {
      const full = await panel.api(`/api/generation/plans/${encodeURIComponent(plan.id)}`);
      const items = full.items || [];
      const list = node("ul", null, "preparation-items");
      items.forEach((item) => list.append(preparationItem(item)));
      container.replaceChildren(...(items.length ? [list] : [node("p", "Sin combinaciones nuevas; se reutilizaron preparaciones existentes.", "muted")]));
      details.dataset.loaded = "true";
    } catch (error) { container.replaceChildren(node("p", error.message, "review-mark")); }
    finally { delete details.dataset.loading; }
  }

  async function cancelPlan(plan, button) {
    button.disabled = true;
    message("generation-error", "");
    try {
      await panel.api(`/api/generation/plans/${encodeURIComponent(plan.id)}/cancel`, { method: "POST", body: "{}" });
      await Promise.all([loadPlans(), panel.refreshArticles()]);
      message("generation-notice", `Preparación #${plan.id} cancelada. Su historial permanece disponible.`);
    } catch (error) { message("generation-error", error.message); button.disabled = false; }
  }

  async function loadPlans() {
    const result = await panel.api("/api/generation/plans");
    const plans = result.items || [];
    const fragment = document.createDocumentFragment();
    plans.forEach((plan) => {
      const entry = node("article", null, "preparation-entry");
      const heading = node("div", null, "section-title");
      heading.append(node("h3", `Preparación #${plan.id}`), node("span", plan.status === "cancelled" ? "Cancelada" : "Preparada · sin generar", "badge"));
      entry.append(heading, node("p", `${panel.capturedDate(plan.created_at)} · ${plan.item_count ?? plan.pair_count ?? plan.items?.length ?? 0} combinaciones`, "muted hint"));
      const details = node("details");
      const content = node("div");
      details.append(node("summary", "Ver artículos y versiones conservadas"), content);
      details.addEventListener("toggle", () => loadPlanDetails(plan, details, content));
      entry.append(details);
      if (plan.status === "prepared") {
        const cancel = node("button", "Cancelar preparación");
        cancel.type = "button";
        cancel.addEventListener("click", () => cancelPlan(plan, cancel));
        entry.append(cancel);
      }
      fragment.append(entry);
    });
    if (!plans.length) fragment.append(node("p", "Todavía no hay preparaciones. Seleccione artículos en Revisión de datos y pulse «Adaptar seleccionados».", "muted"));
    $("preparation-list").replaceChildren(fragment);
  }

  async function loadSavedPrompt() {
    const details = $("configuration-prompt-detail");
    if (!details.open || details.dataset.loaded || !state.configurationId) return;
    const id = state.configurationId;
    details.dataset.loaded = "true";
    $("configuration-prompt-text").textContent = "Cargando el prompt guardado…";
    try {
      const context = await panel.api(`/api/generation/configurations/${encodeURIComponent(id)}`);
      if (String(state.configurationId) !== String(id)) return;
      const prompt = context.prompt;
      $("configuration-prompt-label").textContent = `${prompt.name} · Versión ${prompt.version} · ${prompt.status === "reviewed" ? "Revisado" : "Borrador"}`;
      $("configuration-prompt-text").textContent = `INSTRUCCIONES DEL SISTEMA\n\n${prompt.system_prompt || "Pendientes de plantilla."}\n\nPLANTILLA DEL MENSAJE\n\n${prompt.template || "Pendiente de definición."}`;
    } catch (error) {
      if (String(state.configurationId) === String(id)) {
        $("configuration-prompt-text").textContent = error.message;
        delete details.dataset.loaded;
      }
    }
  }

  const tabs = [...document.querySelectorAll("[data-generation-tab]")];
  tabs.forEach((button, index) => {
    button.addEventListener("click", () => switchTab(button.dataset.generationTab));
    button.addEventListener("keydown", (event) => {
      let next;
      if (event.key === "ArrowRight") next = (index + 1) % tabs.length;
      else if (event.key === "ArrowLeft") next = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") next = 0;
      else if (event.key === "End") next = tabs.length - 1;
      else return;
      event.preventDefault();
      tabs[next].focus();
      switchTab(tabs[next].dataset.generationTab);
    });
  });
  window.addEventListener("panel:section", (event) => {
    if (event.detail.name === "generation") loadCatalog().catch((error) => message("generation-error", error.message));
  });
  [
    ["destination", fillDestination], ["prompt", fillPrompt], ["configuration", fillConfiguration],
  ].forEach(([kind, fill]) => {
    $(`${kind}-picker`).addEventListener("change", () => fill($(`${kind}-picker`).value));
    $(`${kind}-new`).addEventListener("click", () => { fill(null); $(`${kind}-name`).focus(); });
  });
  $("destination-form").addEventListener("submit", (event) => {
    event.preventDefault();
    save("destination", "destinations", {
      name: $("destination-name").value.trim(), site_url: $("destination-url").value.trim(),
      locale: $("destination-locale").value.trim(), notes: $("destination-notes").value,
      enabled: $("destination-enabled").checked,
    });
  });
  $("prompt-form").addEventListener("submit", (event) => {
    event.preventDefault();
    save("prompt", "prompts", {
      name: $("prompt-name").value.trim(), description: $("prompt-description").value,
      system_prompt: $("prompt-system").value, template: $("prompt-template").value,
      status: $("prompt-status").value, archived: $("prompt-archived").checked,
    });
  });
  $("configuration-form").addEventListener("submit", (event) => {
    event.preventDefault();
    save("configuration", "configurations", {
      name: $("configuration-name").value.trim(), destination_id: Number($("configuration-destination").value),
      prompt_version_id: Number($("configuration-prompt").value), provider: $("configuration-provider").value,
      model: $("configuration-model").value.trim(), notes: $("configuration-notes").value,
      enabled: $("configuration-enabled").checked,
    });
  });
  $("adapt-selected").addEventListener("click", openDialog);
  ["adapt-model", "adapt-custom-model"].forEach((id) => $(id).addEventListener("input", () => {
    $("preparation-preview").hidden = true;
    message("adapt-notice", "");
    message("adapt-error", "");
    syncDialog();
  }));
  $("close-adapt-dialog").addEventListener("click", () => $("adapt-dialog").close());
  $("adapt-dialog").addEventListener("cancel", (event) => { if (state.dialogBusy) event.preventDefault(); });
  $("preview-preparation").addEventListener("click", () => prepare("preview"));
  $("save-preparation").addEventListener("click", () => prepare("save"));
  $("refresh-plans").addEventListener("click", () => loadPlans().catch((error) => message("generation-error", error.message)));
  $("configuration-prompt-detail").addEventListener("toggle", loadSavedPrompt);
})();
