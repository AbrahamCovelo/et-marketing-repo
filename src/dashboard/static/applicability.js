"use strict";

(() => {
  const $ = (id) => document.getElementById(id);
  const panel = window.ETPanel;
  const state = { status: null, busy: false, loading: false, entryId: 0 };
  const decisions = { adapt: "Adaptar", omit: "Omitir todo el artículo", needs_verification: "Pendiente de verificación" };

  function message(id, value) {
    $(id).textContent = value || "";
    $(id).hidden = !value;
  }

  function node(tag, text) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    return element;
  }

  function busy(value) {
    state.busy = value;
    $("applicability-lookup-controls").disabled = value;
    $("applicability-review-controls").disabled = value;
  }

  function invalidate() {
    state.status = null;
    $("applicability-context").hidden = true;
    $("applicability-review-form").hidden = true;
    message("applicability-error", "");
    message("applicability-notice", "");
  }

  function field(parent, labelText, key, value, maximum, type = "text") {
    const label = node("label", labelText);
    const input = node(type === "textarea" ? "textarea" : "input");
    input.id = `applicability-entry-${++state.entryId}`;
    label.htmlFor = input.id;
    if (type === "textarea") input.rows = 2;
    else input.type = type;
    if (maximum) input.maxLength = maximum;
    input.dataset.field = key;
    input.required = true;
    input.value = value || "";
    label.append(input);
    parent.append(label);
    return input;
  }

  function addEntry(kind, value = {}, focus = false) {
    const evidence = kind === "evidence";
    const container = $(evidence ? "applicability-evidence" : "applicability-exclusions");
    if (container.children.length >= (evidence ? 30 : 40)) {
      message("applicability-error", evidence ? "Puede registrar hasta 30 fuentes." : "Puede registrar hasta 40 apartados excluidos.");
      return;
    }
    const entry = node("fieldset");
    entry.className = "applicability-entry";
    entry.append(node("legend", evidence ? "Fuente consultada" : "Apartado excluido"));
    let first;
    if (evidence) {
      const grid = node("div");
      grid.className = "applicability-grid applicability-evidence-grid";
      entry.append(grid);
      first = field(grid, "URL oficial (HTTPS)", "url", value.url, 2000, "url");
      field(grid, "Fecha de consulta", "checked_at", value.checked_at, null, "date");
      field(entry, "Información comprobada y su relación con la decisión", "summary", value.summary, 4000, "textarea");
    } else {
      first = field(entry, "Título o identificación del apartado", "section", value.section, 1500);
      field(entry, "Motivo de exclusión", "reason", value.reason, 2000, "textarea");
    }
    const remove = node("button", evidence ? "Quitar fuente de esta revisión" : "Quitar apartado de esta revisión");
    remove.type = "button";
    remove.addEventListener("click", () => {
      entry.remove();
      $(evidence ? "applicability-add-evidence" : "applicability-add-exclusion").focus();
    });
    entry.append(remove);
    container.append(entry);
    if (focus) first.focus();
  }

  function collectEntries(id) {
    return Array.from($(id).children, (entry) => Object.fromEntries(
      Array.from(entry.querySelectorAll("[data-field]"), (input) => [input.dataset.field, input.value.trim()]),
    ));
  }

  function setCheck(id, value) {
    $(id).value = value === true ? "yes" : value === false ? "no" : "";
  }

  function check(id) {
    return $(id).value === "yes" ? true : $(id).value === "no" ? false : null;
  }

  function render(status) {
    state.status = status;
    const context = status.context;
    $("applicability-article-title").textContent = `#${context.article_id} · ${context.article_title} · ${context.name} (${context.locale})`;
    const label = status.state === "not_reviewed" ? "Todavía no hay revisión para este artículo y configuración."
      : status.state === "stale" ? "La revisión anterior quedó desactualizada. Revise de nuevo el origen y la configuración actuales."
      : `Revisión vigente: ${decisions[status.current_review.decision]}.`;
    const availability = !context.configuration_enabled || !context.destination_enabled || context.prompt_archived
      ? " La configuración, el destino o el prompt están desactivados; no se permite redactar."
      : status.allowed ? " Este control permite pasar a la redacción cuando se ejecute una prueba autorizada."
      : " La redacción queda bloqueada hasta tener una revisión vigente que permita adaptar.";
    $("applicability-status").textContent = label + availability;
    const latest = status.latest_review;
    message("applicability-latest", latest
      ? `Última revisión: ${decisions[latest.decision]} · ${latest.reviewer} · ${panel.capturedDate(latest.created_at)}. ${latest.reason}` : "");
    $("applicability-context").hidden = false;
    $("applicability-review-form").reset();
    $("applicability-evidence").replaceChildren();
    $("applicability-exclusions").replaceChildren();
    const current = status.current_review;
    if (current) {
      $("applicability-decision").value = current.decision;
      $("applicability-reviewer").value = current.reviewer;
      $("applicability-question").value = current.central_question;
      $("applicability-reason").value = current.reason;
      setCheck("applicability-central", current.checks.central_applicable);
      setCheck("applicability-exclusive", current.checks.whole_article_exclusive);
      setCheck("applicability-alternative", current.checks.alternative_applicable);
      $("applicability-verified").checked = current.checks.essential_information_verified;
      current.excluded_sections.forEach((entry) => addEntry("exclusion", entry));
      current.evidence.forEach((entry) => addEntry("evidence", entry));
    }
    $("applicability-review-form").hidden = false;
  }

  async function consult(event) {
    event.preventDefault();
    if (state.busy || !$("applicability-lookup-form").reportValidity()) return;
    const query = new URLSearchParams({ article_id: $("applicability-article").value, configuration_id: $("applicability-configuration").value });
    invalidate();
    busy(true);
    try { render(await panel.api(`/api/generation/applicability?${query}`)); }
    catch (error) { message("applicability-error", error.message); }
    finally { busy(false); }
  }

  async function save(event) {
    event.preventDefault();
    if (state.busy || !state.status || !$("applicability-review-form").reportValidity()) return;
    const status = state.status;
    const payload = {
      article_id: status.context.article_id,
      configuration_id: status.context.configuration_id,
      context_fingerprint: status.context_fingerprint,
      review: {
        decision: $("applicability-decision").value,
        reviewer: $("applicability-reviewer").value.trim(),
        central_question: $("applicability-question").value.trim(),
        reason: $("applicability-reason").value.trim(),
        checks: {
          central_applicable: check("applicability-central"),
          whole_article_exclusive: check("applicability-exclusive"),
          alternative_applicable: check("applicability-alternative"),
          essential_information_verified: $("applicability-verified").checked,
        },
        excluded_sections: collectEntries("applicability-exclusions"),
        evidence: collectEntries("applicability-evidence"),
      },
    };
    busy(true);
    message("applicability-error", "");
    message("applicability-notice", "");
    try {
      const response = await panel.api("/api/generation/applicability", { method: "POST", body: JSON.stringify(payload) });
      render(response.status);
      message("applicability-notice", "Revisión guardada. Las revisiones anteriores se conservan; no se ha generado ni enviado ningún artículo.");
      window.dispatchEvent(new CustomEvent("panel:applicability", { detail: response.status }));
    } catch (error) { message("applicability-error", error.message); }
    finally { busy(false); }
  }

  async function useCurrent(quiet = false) {
    if (state.busy) return;
    let id = panel.currentArticleId?.();
    if (!id) {
      const ids = await panel.selectedIds();
      if (ids.length === 1) id = ids[0];
    }
    if (!id) {
      if (!quiet) message("applicability-error", "Abra un artículo en Revisión de datos o seleccione uno solo para cargar su ID.");
      return;
    }
    if (String(id) !== $("applicability-article").value) {
      $("applicability-article").value = id;
      invalidate();
    }
    if (!quiet) $("applicability-configuration").focus();
  }

  async function initialize() {
    if (state.loading || state.busy) return;
    state.loading = true;
    try {
      const catalog = await panel.api("/api/generation/catalog");
      const select = $("applicability-configuration");
      const previous = select.value;
      const placeholder = node("option", "Seleccione una configuración");
      placeholder.value = "";
      select.replaceChildren(placeholder);
      catalog.configurations.forEach((configuration) => {
        const option = node("option", `${configuration.name} · ${configuration.locale}${configuration.enabled ? "" : " · desactivada"}`);
        option.value = configuration.id;
        select.append(option);
      });
      select.value = previous;
      if (select.value !== previous) invalidate();
      if (!$("applicability-article").value) await useCurrent(true);
    } catch (error) { message("applicability-error", error.message); }
    finally { state.loading = false; }
  }

  $("applicability-lookup-form").addEventListener("submit", consult);
  $("applicability-review-form").addEventListener("submit", save);
  $("applicability-article").addEventListener("input", invalidate);
  $("applicability-configuration").addEventListener("change", invalidate);
  $("applicability-use-current").addEventListener("click", () => useCurrent().catch((error) => message("applicability-error", error.message)));
  $("applicability-add-exclusion").addEventListener("click", () => addEntry("exclusion", {}, true));
  $("applicability-add-evidence").addEventListener("click", () => addEntry("evidence", {}, true));
  window.addEventListener("panel:section", (event) => {
    if (event.detail.name === "generation") initialize();
  });
})();
