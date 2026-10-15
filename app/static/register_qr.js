// QR-registration page: the poller, and the "copy the link" button.
//
// While the code is pending, ask the server every few seconds whether an
// existing student has confirmed it. As soon as the status flips, reload the
// page: the server then renders the "set a password" step.
//
// The same page offers the code as a link, for the case of one device and two
// windows (the student who confirms opens the link in a private window). The
// link has to be selected and copied, and doing that by hand on a phone is
// exactly the sort of thing that goes wrong in front of an audience.
//
// The script only loads on the pending branch of register_qr.html. It lives in
// its own file because the CSP allows "script-src 'self'" and refuses inline
// <script> -- moving it here is what lets it run at all.
(function () {
    const POLL_INTERVAL_MS = 4000;

    const hint = document.getElementById("qr-poll-hint");

    // Copy the link. navigator.clipboard needs a secure context (https, or
    // localhost); without it (plain http on a phone) fall back to selecting the
    // text so the student can copy it with a long press.
    const copyButton = document.getElementById("qr-copy");
    const copyStatus = document.getElementById("qr-copy-status");
    if (copyButton) {
        copyButton.addEventListener("click", async function () {
            const target = document.getElementById(
                copyButton.getAttribute("data-copy-target")
            );
            if (!target) {
                return;
            }
            const url = target.textContent.trim();
            try {
                if (!navigator.clipboard || !window.isSecureContext) {
                    throw new Error("clipboard unavailable");
                }
                await navigator.clipboard.writeText(url);
                copyStatus.textContent = "Ссылка скопирована.";
            } catch (e) {
                const range = document.createRange();
                range.selectNodeContents(target);
                const selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
                copyStatus.textContent =
                    "Скопируйте ссылку вручную: она уже выделена.";
            }
        });
    }


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
