// QR-registration poller.
//
// While the code is pending, ask the server every few seconds whether an
// existing student has confirmed it. As soon as the status flips, reload the
// page: the server then renders the "set a password" step.
//
// The script only loads on the pending branch of register_qr.html. It lives in
// its own file because the CSP allows "script-src 'self'" and refuses inline
// <script> -- moving it here is what lets it run at all.
(function () {
    const POLL_INTERVAL_MS = 4000;

    const hint = document.getElementById("qr-poll-hint");

    async function poll() {
        try {
            const response = await fetch("/register/qr/status", { cache: "no-store" });
            if (!response.ok) {
                return;
            }
            const data = await response.json();
            if (data.status && data.status !== "pending") {
                window.location.reload();
            }
        } catch (e) {
            // Transient network error: keep polling, and say so rather than
            // leaving the student staring at a hint that stopped being true.
            if (hint) {
                hint.textContent = "Связь прервалась, пробуем снова…";
            }
        }
    }

    setInterval(poll, POLL_INTERVAL_MS);
})();
