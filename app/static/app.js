// Everything the pages do without leaving themselves.
//
// Seven kinds of form carry `data-async` (what to do) and `data-api` (where the
// JSON twin lives): a like, a new post, a comment, a question to the assistant,
// an avatar change, an alias change and an application to the dev team. Each
// one keeps a real `action` and a real page behind it, so if this file never
// loads the site is exactly what it was -- the fetch is the short way round,
// not the only way.
//
// Two rules run through the whole file.
//
// **The page moves first, then is told the truth.** A like fills its heart and
// bumps its counter on the tap, because waiting for a round trip is not what a
// tap means. The answer then overwrites those numbers with the server's, and a
// refusal puts them back exactly as they were.
//
// **A refusal is content, not an error.** The server answers 200 with
// `{"ok": false, "error": "..."}` for anything the student could fix -- too
// long, a phone number in the text, an alias already taken -- and that sentence
// is written next to the form, in the wording the service itself chose. Only a
// lost session, a stale CSRF token or a dead network falls through to a toast,
// because there is nothing to fix in the form when the form is not the problem.

(function () {
    "use strict";

    /* Toasts ------------------------------------------------------------------- */

    const host = document.getElementById("toast-host");

    // One toast at a time. A stack of them over a phone's bottom bar is a wall
    // of text, and the second message is almost always the first one repeated.
    function toast(message, kind) {
        if (!host || !message) return;

        host.textContent = "";
        const box = document.createElement("div");
        box.className = "toast toast--" + (kind || "success");
        box.textContent = message;
        host.appendChild(box);

        window.setTimeout(function () {
            box.classList.add("is-leaving");
            box.addEventListener("animationend", function () {
                box.remove();
            }, { once: true });
            // A safety net for reduced motion, where the leaving animation is
            // switched off and `animationend` therefore never arrives.
            window.setTimeout(function () {
                box.remove();
            }, 600);
        }, 2600);
    }

    /* Dialogs and demo buttons -------------------------------------------------- */

    document.querySelectorAll("dialog.sheet").forEach(function (dialog) {
        // A tap on the backdrop closes, the same as Escape does. The event
        // fires on the dialog itself only when the backdrop was the target --
        // a click on anything inside it bubbles up with that thing as target.
        dialog.addEventListener("click", function (event) {
            if (event.target === dialog) dialog.close();
        });
    });

    document.addEventListener("click", function (event) {
        const target = event.target;
        if (!target || !target.closest) return;

        // The header's menu and the "..." of a card are <details>, so they open
        // and close with no script at all -- but a <details> that stays open
        // while the reader taps the page next to it reads as stuck. Clicking
        // anywhere outside closes it, and Escape closes it too (below); without
        // this file the menu simply closes where it was opened.
        document.querySelectorAll("details.burger[open], details.menu[open]").forEach(
            function (menu) {
                if (!menu.contains(target)) menu.removeAttribute("open");
            }
        );

        // The dev-apply button is a real link to a real page (/me/dev-apply)
        // *and* a dialog opener, and which one happens is decided right here:
        // with showModal() the page stays where it is, without it the link does
        // what a link does. That is the whole ladder -- no button that does
        // nothing without a script.
        const opener = target.closest("[data-dialog-open]");
        if (opener) {
            const dialog = document.getElementById(opener.getAttribute("data-dialog-open"));
            if (dialog && typeof dialog.showModal === "function") {
                event.preventDefault();
                if (!dialog.open) dialog.showModal();
                return;
            }
        }

        const closer = target.closest("[data-dialog-close]");
        if (closer) {
            const dialog = closer.closest("dialog");
            if (dialog) dialog.close();
            return;
        }

        // A suggested question is a button, not a label: it puts its own text
        // in the field and asks it through the same form, so the tap and the
        // typed question take exactly one path through the code.
        const suggestion = target.closest("[data-ask]");
        if (suggestion) {
            const field = document.getElementById("question");
            const form = document.querySelector('form[data-async="assistant"]');
            if (field && form) {
                field.value = suggestion.textContent.trim();
                form.requestSubmit();
            }
            return;
        }

        // A button that only has something to say (the camera on the profile:
        // uploading a picture does not exist yet, and a button that silently
        // does nothing is worse than one that admits it).
        const talker = target.closest("[data-toast]");
        if (talker) {
            toast(
                talker.getAttribute("data-toast"),
                talker.getAttribute("data-toast-kind")
            );
        }
    });

    // Escape closes the menus. On a <dialog> the browser does this by itself --
    // that is one of the reasons the sheets are dialogs -- but a <details> has no
    // idea the key exists.
    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") return;
        document.querySelectorAll("details.burger[open], details.menu[open]").forEach(
            function (menu) {
                menu.removeAttribute("open");
            }
        );
    });

    /* The round trip ------------------------------------------------------------ */

    function send(form) {
        return fetch(form.getAttribute("data-api"), {
            method: "POST",
            body: new FormData(form),
            credentials: "same-origin",
            headers: { Accept: "application/json" },
        }).then(function (response) {
            return response.json().then(
                function (payload) {
                    return { payload: payload };
                },
                // Not JSON at all: a 403 CSRF page, a 404, or a crash behind
                // the endpoint. The script has nothing to say about any of
                // them that the student can act on, so every handler treats a
                // missing payload as a lost write.
                function () {
                    return { payload: null };
                }
            );
        });
    }

    // Where a refusal is written: a slot of its own next to the form it belongs
    // to (see `data-error-slot` in the templates), so "слишком длинно" lands
    // under the field rather than in a toast that hides what was typed.
    function showError(form, message) {
        const slot = form.parentElement
            ? form.parentElement.querySelector("[data-error-slot]")
            : null;
        if (!slot) return false;
        slot.textContent = message;
        slot.hidden = false;
        return true;
    }

    function clearError(form) {
        const slot = form.parentElement
            ? form.parentElement.querySelector("[data-error-slot]")
            : null;
        if (!slot) return;
        slot.textContent = "";
        slot.hidden = true;
    }

    // True when the answer was a refusal -- the write did not happen and the
    // caller must not touch the page as if it had. Returns false for a success,
    // so a handler reads `if (refused(form, payload)) return;`.
    function refused(form, payload) {
        if (payload && payload.ok) return false;

        const message =
            (payload && payload.error) ||
            "Не получилось сохранить — обновите страницу и попробуйте снова";
        if (!showError(form, message)) toast(message, "error");
        return true;
    }

    // Per-form sequence numbers. A form with several requests in the air at once
    // -- the heart, tapped twice quickly -- must let only the newest answer
    // write, or the slower one lands last and the heart disagrees with the
    // server.
    function bump(form) {
        const next = (parseInt(form.dataset.sequence || "0", 10) || 0) + 1;
        form.dataset.sequence = String(next);
        return next;
    }

    function stale(form, sequence) {
        return String(sequence) !== form.dataset.sequence;
    }

    /* One-line composers --------------------------------------------------------- */

    // A composer is one line until there is a reason for more: the room a thought
    // takes is borrowed from the lines it actually needs instead of being
    // reserved by an empty box. Four is the ceiling -- past that the field
    // scrolls, because a composer taller than the phone turns the post below it
    // into a footnote.
    const COMPOSER_ROWS = 4;

    function growField(field) {
        const style = window.getComputedStyle(field);
        const line = parseFloat(style.lineHeight) || 20;
        const chrome =
            parseFloat(style.paddingTop) + parseFloat(style.paddingBottom) +
            parseFloat(style.borderTopWidth) + parseFloat(style.borderBottomWidth);
        const max = line * COMPOSER_ROWS + chrome;

        // Measured from "auto", not from the current height: a field typed into
        // and then emptied has to come back down, and scrollHeight of a fixed
        // height is that same fixed height.
        field.style.height = "auto";
        const wanted = Math.min(field.scrollHeight, max);
        field.style.height = Math.max(wanted, line + chrome) + "px";
        field.style.overflowY = field.scrollHeight > max ? "auto" : "hidden";
    }

    // The send button follows the field: an empty composer has nothing to send.
    // It is only ever disabled from here -- in the markup it is a plain button,
    // so a browser that never runs this file keeps a form it can submit.
    function syncComposer(form) {
        const field = form.querySelector("[data-autogrow]");
        if (!field) return;
        growField(field);
        const button = form.querySelector('button[type="submit"]');
        if (button) button.disabled = field.value.trim() === "";
    }

    document.querySelectorAll("form").forEach(function (form) {
        if (!form.querySelector("[data-autogrow]")) return;
        form.querySelector("[data-autogrow]").addEventListener("input", function () {
            syncComposer(form);
        });
        syncComposer(form);
    });

    /* Likes --------------------------------------------------------------------- */

    function paintLike(button, liked, count) {
        if (!button) return;
        button.setAttribute("aria-pressed", liked ? "true" : "false");
        button.classList.toggle("is-liked", liked);
        const label = button.querySelector(".visually-hidden");
        if (label) {
            label.textContent = liked ? "Убрать отметку «нравится»" : "Нравится";
        }
        const counter = button.querySelector(".action__count");
        if (counter) counter.textContent = String(count);
    }

    function likeHandler(form) {
        const button = form.querySelector(".action--like");
        const counter = button ? button.querySelector(".action__count") : null;
        const was = {
            liked: !!button && button.getAttribute("aria-pressed") === "true",
            count: counter ? parseInt(counter.textContent, 10) || 0 : 0,
        };

        paintLike(button, !was.liked, Math.max(0, was.count + (was.liked ? -1 : 1)));

        const sequence = bump(form);
        return send(form).then(function (result) {
            if (stale(form, sequence)) return;

            const payload = result.payload;
            if (payload && payload.ok) {
                paintLike(button, !!payload.liked, payload.count);
                return;
            }

            paintLike(button, was.liked, was.count);
            toast((payload && payload.error) || "Не удалось поставить отметку", "error");
        });
    }

    /* A new post ---------------------------------------------------------------- */

    function postHandler(form) {
        return send(form).then(function (result) {
            if (refused(form, result.payload)) return;

            const list = document.getElementById("posts");
            if (list) {
                // The "nothing here yet" panel is a lie the moment the card
                // lands next to it, so it goes first.
                const empty = list.querySelector(".empty");
                if (empty) empty.remove();
                list.insertAdjacentHTML("afterbegin", result.payload.html);
            }

            form.reset();
            clearError(form);
            toast("Пост опубликован");
        });
    }

    /* A comment ----------------------------------------------------------------- */

    function commentHandler(form) {
        return send(form).then(function (result) {
            if (refused(form, result.payload)) return;

            const list = document.getElementById("comments");
            if (list) {
                const empty = list.querySelector("[data-comments-empty]");
                if (empty) empty.remove();
                // Oldest first, so a new comment goes under the ones before it.
                list.insertAdjacentHTML("beforeend", result.payload.html);
            }

            const counter = document.getElementById("comments-count");
            if (counter) counter.textContent = String(result.payload.count);

            form.reset();
            clearError(form);
            toast("Комментарий добавлен");
        });
    }

    /* The assistant ------------------------------------------------------------- */

    const SOURCE_LABELS = { db: "база школы", llm: "модель", help: "подсказка" };

    // A bubble is assembled from elements rather than from an HTML string, and
    // the only thing ever written into it as markup is the answer itself -- it
    // arrives already rendered by the server (app.core.markdown), which escapes
    // the model's text before adding a tag of its own. Everything the script
    // has to say goes in as `textContent`, so a refusal cannot become markup
    // either. `addBubble` hands back the body element for the caller to fill.
    function addBubble(thread, kind, source) {
        const node = document.createElement("article");
        node.className = "bubble bubble--" + kind;

        const body = document.createElement("div");
        body.className = "md";
        node.appendChild(body);

        if (source) {
            const tag = document.createElement("p");
            tag.className = "bubble-source";
            tag.textContent = source;
            node.appendChild(tag);
        }

        thread.appendChild(node);
        return body;
    }

    function typingIndicator(thread) {
        const node = document.createElement("article");
        node.className = "bubble bubble--assistant bubble--typing";
        // Announced, not just shown: three dots are decoration, and a screen
        // reader is owed the sentence.
        node.setAttribute("aria-label", "Ассистент печатает");
        node.setAttribute("role", "status");

        const dots = document.createElement("span");
        dots.className = "typing";
        for (let index = 0; index < 3; index += 1) {
            dots.appendChild(document.createElement("span"));
        }
        node.appendChild(dots);

        thread.appendChild(node);
        node.scrollIntoView({ block: "nearest" });
        return node;
    }

    function assistantHandler(form) {
        const thread = document.getElementById("chat");
        const field = form.querySelector("textarea, input[name=question]");
        const question = field ? field.value.trim() : "";
        if (!thread || !question) return Promise.resolve();

        const asked = addBubble(thread, "user");
        asked.textContent = question;

        // The request is built here, while the field still holds the question,
        // and only then is the field cleared.
        const request = send(form);
        if (field) field.value = "";

        const typing = typingIndicator(thread);
        return request.then(function (result) {
            typing.remove();
            const payload = result.payload;

            if (payload && payload.ok) {
                const answer = addBubble(thread, "assistant", SOURCE_LABELS[payload.source] || "");
                answer.innerHTML = payload.html;
            } else {
                const failure = addBubble(thread, "assistant bubble--error");
                failure.textContent =
                    (payload && payload.error) || "Ответ не пришёл — попробуйте ещё раз";
            }
            typing.scrollIntoView({ block: "nearest" });
        });
    }

    /* Profile: avatar and alias -------------------------------------------------- */

    // The picture in the header is replaced with the one just chosen, cloned
    // from the gallery option itself -- the same markup the server would print,
    // so the two can never disagree about what a key means.
    function paintHeroAvatar(form) {
        const holder = document.querySelector(".profile-photo");
        const chosen = form.querySelector("input[name=avatar_key]:checked");
        if (!holder || !chosen) return;

        const option = chosen.closest("label");
        const picture = option ? option.querySelector(".avatar") : null;
        const current = holder.querySelector(".avatar");
        if (picture && current) holder.replaceChild(picture.cloneNode(true), current);
    }

    // Saving a setting closes the sheet it was saved from: the thing just
    // changed is drawn on the page behind it (the picture in the header, the
    // name above it), and a modal left standing over the change hides the only
    // proof the student has that it worked. A refusal never gets here.
    function closeSheet(form) {
        const dialog = form.closest("dialog");
        if (dialog) dialog.close();
    }

    function avatarHandler(form) {
        return send(form).then(function (result) {
            if (refused(form, result.payload)) return;
            paintHeroAvatar(form);
            clearError(form);
            closeSheet(form);
            toast(result.payload.message || "Аватарка сохранена");
        });
    }

    function aliasHandler(form) {
        return send(form).then(function (result) {
            if (refused(form, result.payload)) return;

            const alias = result.payload.alias;
            const field = form.querySelector("input[name=new_alias]");
            if (field && alias) field.value = alias;

            const name = document.getElementById("profile-alias");
            if (name && alias) name.textContent = alias;

            clearError(form);
            closeSheet(form);
            toast(result.payload.message || "Псевдоним сохранён");
        });
    }

    // The class is written back into the row that names it on the profile, the
    // same words the server would print on the next visit. A cleared class says
    // "Класс не указан" rather than leaving the row empty: the row is also the
    // door to the section, and a door with no sign on it explains nothing. The
    // wording is shared with the template by hand -- the two are three words
    // long and change together.
    function classHandler(form) {
        return send(form).then(function (result) {
            if (refused(form, result.payload)) return;

            const row = document.getElementById("profile-class");
            if (row) row.textContent = result.payload.class_name || "Класс не указан";

            clearError(form);
            closeSheet(form);
            toast(result.payload.message || "Класс сохранён");
        });
    }

    /* The dev team --------------------------------------------------------------- */

    function teamHandler(form) {
        return send(form).then(function (result) {
            if (refused(form, result.payload)) return;

            closeSheet(form);
            form.reset();
            clearError(form);
            toast(result.payload.message || "Заявка отправлена");
        });
    }

    /* Wiring --------------------------------------------------------------------- */

    const HANDLERS = {
        like: likeHandler,
        post: postHandler,
        comment: commentHandler,
        assistant: assistantHandler,
        avatar: avatarHandler,
        alias: aliasHandler,
        class: classHandler,
        team: teamHandler,
    };

    // One listener for the whole document rather than one per form: a post that
    // comes back from the server brings its own like form with it, and a
    // handler bound at load time would never see that one.
    document.addEventListener("submit", function (event) {
        const form = event.target;
        if (!form || !form.hasAttribute || !form.hasAttribute("data-async")) return;

        const handler = HANDLERS[form.getAttribute("data-async")];
        if (!handler || !form.getAttribute("data-api")) return;

        // Past this line the page stays where it is, whatever happens next.
        event.preventDefault();

        const button = form.querySelector("button[type=submit], button:not([type])");
        if (button) button.disabled = true;

        handler(form)
            .catch(function () {
                // A dropped connection. The optimistic paint is already on
                // screen; the next attempt corrects it either way.
                toast("Нет связи — попробуйте ещё раз", "error");
            })
            .then(function () {
                if (button) button.disabled = false;
                // A composer clears itself once it has published, and an empty
                // field has nothing to send: here the send button is switched
                // off again rather than left live over a blank line, and the
                // field drops back to the one row it started as. A no-op for
                // every form that is not a composer.
                syncComposer(form);
            });
    });
})();
