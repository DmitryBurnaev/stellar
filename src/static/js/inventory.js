document.querySelectorAll("[data-component-search]").forEach((input) => {
    input.addEventListener("input", () => {
        const term = input.value.trim().toLowerCase();
        input.closest("details").querySelectorAll("[data-component]").forEach((row) => {
            row.hidden = !row.dataset.component.includes(term);
        });
    });
});

const pendingScan = document.querySelector("[data-scan-pending]");
if (pendingScan) {
    window.setTimeout(() => pendingScan.requestSubmit(), 5000);
}
