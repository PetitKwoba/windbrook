function money(value) {
  const amount = Number(String(value || "0").replace(/[$,]/g, ""));
  if (!Number.isFinite(amount)) return 0;
  return amount;
}

function formatMoney(value) {
  return value.toLocaleString(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 0 });
}

function updateLiveTotals() {
  const form = document.querySelector("[data-report-form]");
  if (!form) return;
  const inflow = money(form.querySelector("[data-calc='inflow']")?.value);
  const outflow = money(form.querySelector("[data-calc='outflow']")?.value);
  let deductibles = 0;
  form.querySelectorAll("[data-deductible]").forEach((input) => deductibles += money(input.value));
  const excess = inflow - outflow;
  const target = outflow * 6 + deductibles;
  const excessNode = document.getElementById("live-excess");
  const targetNode = document.getElementById("live-target");
  if (excessNode) excessNode.textContent = formatMoney(excess);
  if (targetNode) targetNode.textContent = formatMoney(target);
}

function addRow(tableName) {
  const table = document.querySelector(`table[data-dynamic='${tableName}']`);
  if (!table) return;
  const max = Number(table.dataset.max || "0");
  const tbody = table.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr"));
  if (rows.length >= max || rows.length === 0) return;
  const next = rows.length + 1;
  const clone = rows[rows.length - 1].cloneNode(true);
  clone.querySelectorAll("input, select").forEach((field) => {
    field.name = field.name.replace(/_(\d+)_/, `_${next}_`);
    field.value = field.tagName === "SELECT" ? field.value : "";
  });
  tbody.appendChild(clone);
}

function sortTable(table, columnIndex) {
  const tbody = table.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr"));
  const direction = table.dataset.sortDirection === "asc" ? "desc" : "asc";
  table.dataset.sortDirection = direction;
  rows.sort((a, b) => {
    const av = a.children[columnIndex]?.textContent?.trim() || "";
    const bv = b.children[columnIndex]?.textContent?.trim() || "";
    return direction === "asc" ? av.localeCompare(bv) : bv.localeCompare(av);
  });
  rows.forEach((row) => tbody.appendChild(row));
}

document.addEventListener("click", (event) => {
  const add = event.target.closest("[data-add-row]");
  if (add) addRow(add.dataset.addRow);

  const useLast = event.target.closest("[data-use-last]");
  if (useLast) {
    document.querySelectorAll("[data-last]").forEach((input) => {
      if (!input.value && input.dataset.last) input.value = input.dataset.last;
    });
    updateLiveTotals();
  }

  const header = event.target.closest("th");
  if (header && header.closest("table[data-sortable]")) {
    sortTable(header.closest("table"), Array.from(header.parentElement.children).indexOf(header));
  }
});

document.addEventListener("input", updateLiveTotals);
document.addEventListener("DOMContentLoaded", updateLiveTotals);
