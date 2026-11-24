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
    const updateScan = async () => {
        try {
            const response = await fetch(pendingScan.dataset.scanPending, {cache: "no-store"});
            if (response.redirected || response.status === 401 || response.status === 403) return;
            if (!response.ok) throw new Error("Status request failed");
            const scan = await response.json();
            if (!["submitting", "queued", "running"].includes(scan.status)) {
                window.location.reload();
                return;
            }
            const label = pendingScan.querySelector("[data-scan-status]");
            label.textContent = scan.status === "running"
                ? `running · ${scan.checked} / ${scan.total} ports checked`
                : scan.status;
        } catch {
            // Retry transient network failures on the next poll.
        }
        window.setTimeout(updateScan, 5000);
    };
    window.setTimeout(updateScan, 5000);
}
