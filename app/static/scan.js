// QR scanner for the /scan page. Fully offline: the camera stream is decoded
// in the browser by jsQR (vendored under /static/vendor) and the result is
// posted back to the server. No external service is involved.
//
// Camera access (getUserMedia) needs a secure context: it works on
// http://localhost in development and requires HTTPS in production.

(function () {
    "use strict";

    const video = document.getElementById("scan-video");
    const canvas = document.getElementById("scan-canvas");
    const status = document.getElementById("scan-status");
    const csrfField = document.getElementById("scan-csrf");
    const context = canvas.getContext("2d", { willReadFrequently: true });

    let scanning = false;
    let stream = null;

    function setStatus(text, kind) {
        status.textContent = text;
        status.className = kind === "error" ? "error" : "hint";
    }

    async function submit(payload) {
        scanning = false;
        setStatus("Код найден, подтверждаем…", "hint");
        const body = new URLSearchParams();
        body.append("payload", payload);
        body.append("csrf_token", csrfField ? csrfField.value : "");
        try {
            const response = await fetch("/scan/confirm", {
                method: "POST",
                headers: { "Content-Type": "application/x-www-form-urlencoded" },
                body: body.toString(),
            });
            const data = await response.json();
            if (data.ok) {
                setStatus("Готово! Регистрация подтверждена.", "hint");
            } else {
                setStatus(data.error || "Не удалось подтвердить код.", "error");
            }
        } catch (e) {
            setStatus("Сеть недоступна. Попробуйте ещё раз.", "error");
        }
        // Resume scanning so the next novice can be confirmed without a reload.
        setTimeout(function () {
            scanning = true;
            setStatus("Наведите камеру на QR-код новичка.", "hint");
        }, 2500);
    }

    function tick() {
        if (!scanning) {
            return;
        }
        if (video.readyState === video.HAVE_ENOUGH_DATA) {
            canvas.width = video.videoWidth;
            canvas.height = video.videoHeight;
            context.drawImage(video, 0, 0, canvas.width, canvas.height);
            const image = context.getImageData(0, 0, canvas.width, canvas.height);
            const code = window.jsQR(image.data, image.width, image.height, {
                inversionAttempts: "dontInvert",
            });
            if (code && code.data) {
                submit(code.data);
                return;
            }
        }
        requestAnimationFrame(tick);
    }

    async function start() {
        try {
            stream = await navigator.mediaDevices.getUserMedia({
                video: { facingMode: "environment" },
            });
        } catch (e) {
            setStatus(
                "Не удалось получить доступ к камере. Разрешите доступ или " +
                    "откройте страницу по HTTPS.",
                "error"
            );
            return;
        }
        video.srcObject = stream;
        await video.play();
        scanning = true;
        setStatus("Наведите камеру на QR-код новичка.", "hint");
        requestAnimationFrame(tick);
    }

    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
        setStatus("Браузер не поддерживает доступ к камере.", "error");
        return;
    }
    if (typeof window.jsQR !== "function") {
        setStatus("Не удалось загрузить декодер QR-кодов.", "error");
        return;
    }
    start();
})();
