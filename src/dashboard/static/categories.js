"use strict";

(() => {
  const $ = (id) => document.getElementById(id), panel = window.ETPanel;
  let targetId = null, sequence = 0;
  const node = (tag, text) => { const el = document.createElement(tag); if (text !== undefined) el.textContent = text; return el; };
  function choices(categories, selected) {
    const group = node('div');
    categories.forEach((category) => {
      const label = node('label'), input = node('input');
      label.className = 'check-label'; input.type = 'checkbox'; input.value = category.id;
      input.checked = selected.includes(category.id);
      label.append(input, document.createTextNode(` ${category.name} (${category.slug})`));
      group.append(label);
    });
    return group;
  }
  const checked = (group) => [...group.querySelectorAll('input:checked')].map((input) => Number(input.value));

  function renderPolicy(data) {
    const container = $('category-policy'); container.replaceChildren();
    container.append(node('p', data.fetched_at ? `Última consulta: ${panel.capturedDate(data.fetched_at)}${data.stale ? ' · pendiente de actualizar' : ''}` : 'Todavía no se ha consultado este sitio.'));
    (data.blockers || []).forEach((text) => container.append(node('p', text)));
    if (!data.categories.length) return;
    const form = node('form'), controls = node('fieldset');
    const group = choices(data.categories, data.policy?.allowed_category_ids || []);
    const label = node('label', 'Categoría predeterminada de WordPress que se excluirá');
    const exclude = node('select');
    exclude.append(new Option('Seleccione la categoría predeterminada', ''));
    data.categories.forEach((c) => exclude.append(new Option(c.name, c.id)));
    exclude.value = String(data.wordpress_default_category_id || data.policy?.excluded_default_category_id || '');
    exclude.disabled = !!data.wordpress_default_category_id;
    const syncExcluded = () => group.querySelectorAll('input').forEach((input) => {
      input.disabled = input.value === exclude.value;
      if (input.disabled) input.checked = false;
    });
    exclude.addEventListener('change', syncExcluded); syncExcluded();
    label.append(exclude); controls.append(label);
    if (!data.wordpress_default_category_id) controls.append(node('p', 'La conexión no permite leer la categoría predeterminada de Ajustes → Escritura. Confírmela aquí; no se deduce por su nombre ni por su ID.'));
    controls.append(node('p', 'Categorías permitidas para los artículos:'), group);
    const save = node('button', 'Guardar categorías permitidas'); save.type = 'submit';
    const status = node('p'); status.setAttribute('role', 'status'); controls.append(save); form.append(controls, status); container.append(form);
    form.addEventListener('submit', async (event) => {
      event.preventDefault(); const id = data.target_id; controls.disabled = true;
      try {
        const result = await panel.api(`/api/deployment/targets/${id}/categories/policy`, {method: 'PUT', body: JSON.stringify({allowed_category_ids: checked(group), excluded_default_category_id: Number(exclude.value), catalog_revision: data.catalog_revision})});
        if (id === targetId) { renderPolicy(result); panel.notice('Categorías guardadas localmente. WordPress no se modificó.'); }
      } catch (error) { status.textContent = error.message; }
      finally { controls.disabled = false; }
    });
  }

  async function load(refresh = false) {
    if (!targetId) return;
    const id = targetId, current = ++sequence;
    $('category-refresh').disabled = true;
    try {
      const data = await panel.api(`/api/deployment/targets/${id}/categories${refresh ? '/refresh' : ''}`, refresh ? {method: 'POST', body: '{}'} : {});
      if (current === sequence) renderPolicy(data);
    } catch (error) { if (current === sequence) $('category-policy').textContent = error.message; }
    finally { if (current === sequence) $('category-refresh').disabled = false; }
  }

  async function openTrial(trialId) {
    const dialog = node('dialog'); dialog.className = 'adaptation-dialog';
    dialog.setAttribute('aria-label', `Categorías de la adaptación ${trialId}`);
    const title = node('h3', `Categorías de la adaptación #${trialId}`), content = node('div', 'Cargando…');
    const close = node('button', 'Cerrar'); close.type = 'button'; close.addEventListener('click', () => dialog.close());
    dialog.append(title, node('p', 'Guardar modifica solo la selección local. «Volver a desplegar» enviará las categorías junto con el contenido; puede cambiar la URL pública si incluye la categoría.'), content, close);
    dialog.addEventListener('close', () => dialog.remove()); document.body.append(dialog); dialog.showModal();
    try {
      const result = await panel.api(`/api/generation/trials/${trialId}/categories`), catalog = result.catalog;
      content.replaceChildren();
      if (!catalog.configured) { content.append(node('p', catalog.blockers.join(' ') + ' Configure el destino en la sección WordPress.')); return; }
      const form = node('form'), fields = node('fieldset');
      const group = choices(catalog.categories.filter((c) => catalog.policy.allowed_category_ids.includes(c.id)), result.category_ids);
      const save = node('button', 'Guardar selección local'), status = node('p'); save.type = 'submit'; status.setAttribute('role', 'status');
      fields.append(group, save); form.append(fields, status); content.append(node('p', result.source === 'model' ? 'Clasificación elegida por la API.' : 'Selección local o del envío anterior; puede revisarla.'), form);
      form.addEventListener('submit', async (event) => {
        event.preventDefault(); fields.disabled = true;
        try {
          await panel.api(`/api/generation/trials/${trialId}/categories`, {method: 'PUT', body: JSON.stringify({category_ids: checked(group), catalog_revision: catalog.catalog_revision})});
          status.textContent = 'Selección guardada. WordPress no se modificó.';
        } catch (error) { status.textContent = error.message; }
        finally { fields.disabled = false; }
      });
    } catch (error) { content.textContent = error.message; }
  }
  window.ETCategories = {openTrial};
  window.addEventListener('deployment:target', (event) => { targetId = Number(event.detail.targetId); load(); });
  $('category-refresh').addEventListener('click', () => load(true));
})();
