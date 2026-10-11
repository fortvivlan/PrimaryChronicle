// Shared by the browser and the dependency-free Node checks.
(function (root) {
  "use strict";
  function editionsFor(edition) {
    return edition === "both" ? ["lavr", "ipat"] : [edition];
  }
  function matches(record, filters) {
    const editions = editionsFor(filters.edition);
    if (!editions.some(key => record.editions[key].available)) return false;
    if (filters.semantics.length && !filters.semantics.includes(record.semantics)) return false;
    if (filters.subject && !editions.some(key => record.editions[key].available && record.subjects[key].includes(filters.subject))) return false;
    if (filters.protograph !== null && record.protographKey !== filters.protograph) return false;
    if (record.year === null) return filters.includePreamble;
    return (filters.from === null || record.year >= filters.from) &&
      (filters.to === null || record.year <= filters.to);
  }
  const api = { editionsFor, matches };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ChronicleFilters = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
