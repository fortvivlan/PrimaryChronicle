(async function () {
  "use strict";
  const form = document.getElementById("filters");
  const rows = document.getElementById("rows");
  const status = document.getElementById("status");
  const resultSection = document.querySelector(".results");
  const semanticNames = {
    EXIST: "Экзистенциальность", PLUPERF: "Неактуальное прошлое",
    VERIF: "Верификация", FALS: "Фальсификация", EVID: "Эвиденциальность",
  };
  let records;
  const text = (tag, content, className) => {
    const node = document.createElement(tag);
    node.textContent = content;
    if (className) node.className = className;
    return node;
  };
  function editionCell(record, key) {
    const cell = document.createElement("td");
    const edition = record.editions[key];
    const passage = text("div", "", "manuscript");
    // This HTML is escaped by the extractor; only local formatting tags are emitted.
    passage.innerHTML = edition.html || "—";
    cell.append(passage);
    if (edition.variants) {
      const details = text("details", "", "variants");
      details.append(text("summary", key === "lavr" ? "Варианты · (Т)РА" : "Варианты · ХП"));
      const variants = text("div", "", "manuscript");
      variants.innerHTML = edition.variantsHtml;
      details.append(variants);
      cell.append(details);
    }
    return cell;
  }
  function render() {
    const fromInput = document.getElementById("year-from");
    const toInput = document.getElementById("year-to");
    const from = fromInput.value === "" ? null : Number(fromInput.value);
    const to = toInput.value === "" ? null : Number(toInput.value);
    const invalid = fromInput.validity.badInput || toInput.validity.badInput ||
      (from !== null && !Number.isInteger(from)) || (to !== null && !Number.isInteger(to)) ||
      (from !== null && to !== null && from > to);
    document.getElementById("year-error").hidden = !invalid;
    fromInput.setAttribute("aria-invalid", String(invalid));
    toInput.setAttribute("aria-invalid", String(invalid));
    const filters = {
      edition: form.elements.edition.value,
      semantics: [...form.querySelectorAll('input[name="semantics"]:checked')].map(input => input.value),
      subject: document.getElementById("subject").value,
      protograph: document.getElementById("protograph").value === "all" ? null : document.getElementById("protograph").value,
      from, to, includePreamble: document.getElementById("preamble").checked,
    };
    const keys = ChronicleFilters.editionsFor(filters.edition);
    for (const header of document.querySelectorAll("th[data-edition]")) header.hidden = !keys.includes(header.dataset.edition);
    document.getElementById("edition-summary").textContent = keys.map(key => key === "lavr" ? "Лаврентьевская" : "Ипатьевская").join(" + ");
    const filtered = invalid ? [] : records.filter(record => ChronicleFilters.matches(record, filters));
    const fragment = document.createDocumentFragment();
    for (const record of filtered) {
      const row = document.createElement("tr");
      row.append(text("td", record.id, "row-number"));
      const year = text("td", "");
      year.append(text("span", record.year ?? "Преамбула", "year-label"));
      if (record.folio) year.append(text("span", record.folio, "folio"));
      row.append(year);
      for (const key of keys) row.append(editionCell(record, key));
      const annotation = document.createElement("td");
      annotation.append(text("div", record.syntax || "Не указана", "syntax"));
      const badge = text("span", record.semantics || "Не указана", "semantic-tag" + (record.semantics ? "" : " empty"));
      badge.title = semanticNames[record.semantics] || "Семантическая аннотация не указана в источнике";
      annotation.append(badge);
      row.append(annotation, text("td", record.protograph || "Не указан", "protograph"));
      fragment.append(row);
    }
    rows.replaceChildren(fragment);
    document.getElementById("shown").textContent = filtered.length;
    status.textContent = invalid ? "Исправьте диапазон лет, чтобы увидеть результаты." :
      filtered.length ? `Показано ${filtered.length} из ${records.length} примеров. Чтения младших списков доступны под текстом редакций.` :
      "По выбранным условиям примеров нет. Измените или сбросьте фильтры.";
  }
  form.addEventListener("submit", event => event.preventDefault());
  try {
    const response = await fetch("assets/records.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    records = data.records;
    document.getElementById("corpus-total").textContent = records.length;
    document.getElementById("total").textContent = records.length;
    const options = document.getElementById("semantic-options");
    for (const value of ["EXIST", "PLUPERF", "VERIF", "FALS", "EVID", ""]) {
      const label = document.createElement("label");
      const input = document.createElement("input");
      input.type = "checkbox"; input.name = "semantics"; input.value = value;
      label.append(input, text("span", value || "Не указана"));
      label.title = semanticNames[value] || "Пустая семантическая ячейка в источнике";
      options.append(label);
    }
    const protograph = document.getElementById("protograph");
    const values = [...new Set(records.map(record => record.protographKey))].sort((a,b) => a.localeCompare(b, "ru"));
    for (const value of values) {
      const option = text("option", value || "Не указан"); option.value = value; protograph.append(option);
    }
    const dated = records.filter(record => record.year !== null).map(record => record.year);
    document.getElementById("year-from").placeholder = `От ${Math.min(...dated)}`;
    document.getElementById("year-to").placeholder = `До ${Math.max(...dated)}`;
    form.addEventListener("input", render);
    document.getElementById("reset").addEventListener("click", () => { form.reset(); render(); });
    render();
  } catch (error) {
    status.textContent = "Не удалось загрузить данные. Обновите страницу. Для локального просмотра запустите HTTP-сервер по инструкции в README.";
    status.classList.add("error");
    console.error("Corpus loading failed:", error);
  } finally {
    resultSection.setAttribute("aria-busy", "false");
  }
})();
