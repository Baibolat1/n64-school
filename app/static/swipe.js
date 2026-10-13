// Bottom-bar behaviour for phones, in three parts that share one measurement.
//
// 1. Swipe between sections. The five tabs of the bottom bar (/feed, /schedule,
//    /assistant, /esports, /me) become neighbours: a sideways drag on the page
//    moves to the one next to it, and the page that arrives slides in from the
//    side the finger came from.
//
// 2. The selected tab as a blob. Without this file each tab paints its own
//    highlight (see .is-active in layout.css); with it, the highlight is one
//    drop of light that travels between tabs, including while a swipe is still
//    in progress -- the bar shows where the finger would land before it lands.
//
// 3. The magnifier. An icon under the finger grows, its neighbours grow less,
//    and the growth falls off with distance -- the Dock's magnifying glass.
//    Mouse hover drives it too, so it can be judged on a desktop.
//
// Nothing here is load-bearing: the tabs are real links, the highlight is in
// CSS, and the file is absent on any page without a bottom bar. If the script
// never runs the site is exactly what it was before, minus the gestures.
//
// Navigation is a real page load, so a swipe cannot drag the next page in from
// off screen -- there is no next page yet. The illusion is built from both
// ends: the page being left fades outwards, and the page being arrived at fades
// inwards, the direction carried between them in a sessionStorage flag.

(function () {
    "use strict";

    const nav = document.querySelector(".bottomnav");
    if (!nav) return;

    const links = Array.from(nav.querySelectorAll("a[href]"));
    if (links.length < 2) return;
    const sections = links.map((link) => link.getAttribute("href"));

    const main = document.querySelector("main");
    const calm = window.matchMedia("(prefers-reduced-motion: reduce)");
    const FLAG = "hub64:swipe";

    // The direction is read once, before any early return: a flag that is never
    // consumed would animate some later page load that nothing asked it to.
    let arrived = null;
    try {
        arrived = window.sessionStorage.getItem(FLAG);
        if (arrived) window.sessionStorage.removeItem(FLAG);
    } catch (error) {
        // Private mode without storage: no flag, so no arrival animation.
    }

    // Which section the reader is in. A section owns its own page and
    // everything under it -- /feed owns /feed/posts/7 -- so the match is by
    // prefix. A page that belongs to no section (a post, a teacher, the
    // scanner) has no neighbours and nothing to swipe to, and gets out here.
    const path = window.location.pathname;
    let here = -1;
    sections.forEach((href, index) => {
        if (path === href || path.indexOf(href + "/") === 0) here = index;
    });
    if (here < 0) return;

    /* The lens ----------------------------------------------------------------- */

    // Wider than the tab it stands on, by 6px on each side: the lens is a piece
    // of glass laid over the tab, not a recolouring of it (see .bottomnav__blob).
    const LENS_OVERHANG = 12;

    const blob = document.createElement("span");
    blob.className = "bottomnav__blob";
    blob.setAttribute("aria-hidden", "true");
    nav.insertBefore(blob, nav.firstChild);
    nav.classList.add("is-js");

    // The tabs are flex-sized, so no width is known in CSS and the lens has to
    // be measured. Geometry comes from the tab's own rect rather than from
    // arithmetic on index and count: the tabs are equal, but the gaps between
    // them are the bar's, and a lens that assumed otherwise would drift a pixel
    // per tab until it hung over the wrong label.
    function lensFor(index) {
        const link = links[index];
        if (!link) return null;

        const box = link.getBoundingClientRect();
        if (!box.width) return null; // hidden bar (wide screen): nothing to measure

        const frame = nav.getBoundingClientRect();
        return {
            width: box.width + LENS_OVERHANG,
            // Offsets of an absolutely positioned child are measured from the
            // padding box, so the bar's own border comes off the distance --
            // and the centre is what is kept, because the lens is not the width
            // of the thing it centres on.
            centre: box.left + box.width / 2 - frame.left - nav.clientLeft,
        };
    }

    // It is translated rather than moved with `left`, so the browser animates
    // it on the compositor instead of relaying out the bar at every frame.
    // `instant` is for the first placement and for a resize: the lens is
    // *already* somewhere on those, it does not travel there.
    function place(width, centre, instant) {
        // Both ends are clamped to the bar's inner width. The lens is wider
        // than the tab it centres on, so on the first and the last tab it would
        // otherwise hang over the capsule's rounded end; and a measurement
        // taken while the bar was mid-relayout -- a rotation, the webfont
        // landing a pixel later -- can leave a tab a hair wider than the room
        // left for it. The lens may fall short of either end; it may not cross.
        if (instant) blob.classList.add("is-instant");
        blob.style.width = width + "px";
        const room = Math.max(nav.clientWidth - width, 0);
        const travel = Math.min(Math.max(centre - width / 2, 0), room);
        blob.style.transform = "translate3d(" + travel + "px, 0, 0)";

        if (instant) {
            // Two frames: the first lets the browser paint the lens in place
            // with the transition off, the second hands the element back to
            // CSS so the next move animates.
            window.requestAnimationFrame(() =>
                window.requestAnimationFrame(() => blob.classList.remove("is-instant"))
            );
        }
    }

    function moveBlob(index, instant) {
        const lens = lensFor(index);
        if (lens) place(lens.width, lens.centre, instant);
    }

    moveBlob(here, true);

    /* The magnifier ------------------------------------------------------------ */

    // A finger on the bar grows the tab under it and, less, its neighbours: the
    // growth falls off with distance, which is what makes a Dock feel like a
    // magnifying glass rather than like a hover state. Placed here rather than
    // in a file of its own because it measures the same tabs the blob does --
    // the two would otherwise each keep their own copy of that.
    const MAGNIFY = 0.2; // growth right under the finger (the icon under the lens)
    const LIFT = 2; // px the icon rises, so growing reads as coming up
    const SPREAD = 2.4; // reach of the glass, in tab widths

    const icons = links.map((link) => link.querySelector(".icon"));

    function magnify(pointerX) {
        const boxes = links.map((link) => link.getBoundingClientRect());
        // The falloff is measured in tab widths, not in pixels, so the reach
        // stays the same shape on a narrow phone and a wide one. At 2.4 a tab
        // next to the finger ends up about a tenth larger, the one after it
        // barely registers, and the far ones are untouched.
        // The last fallback is only there so a zero width can never divide: a
        // hidden bar takes no pointer events, but a NaN would poison every
        // icon's scale rather than one frame of one icon.
        const width = boxes[0].width || nav.getBoundingClientRect().width / links.length || 1;
        const reach = width * SPREAD;

        boxes.forEach((box, index) => {
            const icon = icons[index];
            if (!icon) return;

            const distance = Math.abs(pointerX - (box.left + box.width / 2));
            // Squared: a rounded top where the finger is and a tail that is
            // spent by the second neighbour.
            const pull = Math.max(0, 1 - distance / reach) ** 2;

            icon.style.setProperty("--dock-scale", (1 + MAGNIFY * pull).toFixed(3));
            icon.style.setProperty("--dock-lift", (-LIFT * pull).toFixed(2) + "px");
        });
    }

    // Letting go is the animated half: the properties go back to the CSS
    // defaults and the spring in layout.css carries the icons home.
    function settle() {
        nav.classList.remove("is-magnifying");
        icons.forEach((icon) => {
            if (!icon) return;
            icon.style.removeProperty("--dock-scale");
            icon.style.removeProperty("--dock-lift");
        });
    }

    /* The drag on the bar ------------------------------------------------------ */

    // The same gesture as the swipe, told the other way round: there the finger
    // drags the *page* sideways and the lens previews where it would land; here
    // the finger drags the *lens* along the bar and the page follows it on
    // release. Both end in a page load, and both use the same lens.
    //
    // A drag is not a tap: a finger that moved 10px meant to move the lens, and
    // the click the browser is about to fire on the tab it started from would
    // navigate to the wrong place. That click is swallowed -- but only that one,
    // and only if the gesture actually moved.

    const DRAG_SLOP = 6; // px of travel that turns a tap into a drag
    const FLICK = 0.55; // px/ms that carries the lens one tab further
    const FLICK_WINDOW = 140; // ms of movement the flick is measured over

    let dragging = false; // the finger is down on the bar
    let dragged = false; // ...and it has moved far enough to mean it
    let swallowClick = false;
    let samples = [];

    function nearestTab(clientX) {
        let best = here;
        let closest = Infinity;
        links.forEach((link, index) => {
            const box = link.getBoundingClientRect();
            const distance = Math.abs(clientX - (box.left + box.width / 2));
            if (distance < closest) {
                closest = distance;
                best = index;
            }
        });
        return best;
    }

    // How fast the finger was going when it let go: a flick of the thumb is a
    // request for the *next* tab, not for the one under it -- the same reading
    // the page swipe takes, in the other axis.
    function flickDirection() {
        if (samples.length < 2) return 0;
        const last = samples[samples.length - 1];
        const first = samples[0];
        const span = last.t - first.t;
        if (span <= 0 || span > FLICK_WINDOW) return 0;
        const speed = (last.x - first.x) / span;
        return Math.abs(speed) > FLICK ? Math.sign(speed) : 0;
    }

    // The lens follows the finger at the finger's own speed: no lag and no
    // inertia *while* dragging, because a lens that trails the finger makes the
    // next tab feel uncertain. The inertia is on the other end of the gesture --
    // the overshoot of --transition-dock and the flick above.
    function follow(clientX) {
        const lens = lensFor(here);
        if (!lens) return;
        const frame = nav.getBoundingClientRect();
        place(lens.width, clientX - frame.left - nav.clientLeft, false);
    }

    function drop(event) {
        if (!dragging) return;
        dragging = false;
        nav.classList.remove("is-swiping");
        // A tap: the lens has not moved and the link under the finger is about
        // to be followed on its own. Put it back where it belongs and get out.
        if (!dragged) {
            moveBlob(here);
            return;
        }

        swallowClick = true;
        const index = Math.min(
            Math.max(nearestTab(event.clientX) + flickDirection(), 0),
            sections.length - 1
        );
        // Snapped first, navigated second: the spring is 350ms and the page
        // load takes longer than that to paint, so the lens is seen arriving
        // before the page it announces replaces the one underneath it.
        moveBlob(index);
        if (index === here) return;

        // One tab over is a direction, and the arriving page slides in from it
        // (see the page transitions in layout.css). Two is not a direction.
        if (Math.abs(index - here) === 1) {
            try {
                window.sessionStorage.setItem(FLAG, index > here ? "next" : "prev");
            } catch (error) {
                // No storage: the page still changes, it just does not animate.
            }
        }
        window.setTimeout(() => window.location.assign(sections[index]), 200);
    }

    if (!calm.matches) {
        nav.addEventListener("pointerdown", (event) => {
            if (event.pointerType === "mouse" && event.button !== 0) return;
            dragging = true;
            dragged = false;
            swallowClick = false;
            samples = [{ x: event.clientX, t: event.timeStamp }];
            // The finger may leave the bar mid-drag -- it is 68px tall -- and
            // the lens has to keep following it. Not every engine throws here;
            // the one that does keeps the magnifier and loses the capture.
            try {
                nav.setPointerCapture(event.pointerId);
            } catch (error) {
                // No capture: the drag ends where the bar does.
            }
            nav.classList.add("is-magnifying");
            magnify(event.clientX);
        });
        // Also without a button pressed: on a touch screen `pointerdown` has
        // already fired, on a mouse this is the hover the effect is named after.
        nav.addEventListener("pointermove", (event) => {
            nav.classList.add("is-magnifying");
            magnify(event.clientX);
            if (!dragging) return;

            samples.push({ x: event.clientX, t: event.timeStamp });
            if (samples.length > 6) samples.shift();
            if (!dragged && Math.abs(event.clientX - samples[0].x) >= DRAG_SLOP) {
                dragged = true;
                // The lens swells for as long as the drag lasts and springs
                // back on release (see .bottomnav.is-swiping in layout.css).
                nav.classList.add("is-swiping");
            }
            if (dragged) follow(event.clientX);
        });
        nav.addEventListener("pointerup", (event) => {
            settle();
            drop(event);
        });
        nav.addEventListener("pointercancel", () => {
            settle();
            dragging = false;
            dragged = false;
            nav.classList.remove("is-swiping");
            moveBlob(here);
        });
        // Only when no drag is in flight: with the pointer captured the finger
        // can leave the bar and come back, and the icons must not be dropped
        // mid-gesture on the way out.
        nav.addEventListener("pointerleave", () => {
            if (!dragging) settle();
        });
    }

    // The one click that follows a drag, and only that one. Captured on the bar
    // so it never reaches the tab underneath.
    nav.addEventListener(
        "click",
        (event) => {
            if (!swallowClick) return;
            swallowClick = false;
            event.preventDefault();
            event.stopPropagation();
        },
        true
    );

    /* The swipe ---------------------------------------------------------------- */

    const TRIGGER = 64; // px of travel that means "take me there"
    const LOCK = 12; // px before the gesture is read as one direction or the other
    const RESIST = 0.28; // how much of a push past either end the page accepts

    let startX = 0;
    let startY = 0;
    let dx = 0;
    let tracking = false;
    let horizontal = false;
    let preview = -1; // the section the finger is currently pointing at, or -1

    // A gesture that begins in a field, a sideways scroller or on the bar
    // itself belongs to that element, not to the page: selecting text, moving a
    // caret, reading a wide table and rubbing an icon all look like a swipe on
    // the way in. On the bar the gesture is already spoken for -- it is what
    // drives the magnifier.
    function owned(target) {
        return !!(
            target &&
            target.closest &&
            target.closest(".table-scroll, input, textarea, select, .bottomnav")
        );
    }

    function reset() {
        if (main) {
            main.classList.remove("is-dragging");
            main.style.transform = "";
            main.style.opacity = "";
        }
        nav.classList.remove("is-swiping");
        moveBlob(here);
    }

    function onStart(event) {
        if (event.touches.length !== 1) {
            tracking = false;
            return;
        }
        const touch = event.touches[0];
        startX = touch.clientX;
        startY = touch.clientY;
        dx = 0;
        preview = -1;
        horizontal = false;
        // The order being swiped through is the order of the bottom bar, and on
        // a wide screen there is no bottom bar -- the links are all in the top
        // one. Asking the bar whether it is on screen (rather than repeating
        // the breakpoint here) keeps that decision in the stylesheet.
        tracking = nav.getClientRects().length > 0 && !owned(event.target);
    }

    function onMove(event) {
        if (!tracking) return;

        const touch = event.touches[0];
        const mx = touch.clientX - startX;
        const my = touch.clientY - startY;

        if (!horizontal) {
            if (Math.abs(mx) < LOCK && Math.abs(my) < LOCK) return;
            // The page scrolls up and down. Only a clearly sideways gesture is
            // ours; once we claim it, we own the rest of it, and the browser is
            // told so the page does not also scroll under the finger.
            if (Math.abs(my) >= Math.abs(mx)) {
                tracking = false;
                return;
            }
            horizontal = true;
            if (main) main.classList.add("is-dragging");
            // The pill swells for as long as the gesture lasts (see
            // .bottomnav.is-swiping in layout.css) and springs back to its
            // size when it lets go, wherever it lands.
            nav.classList.add("is-swiping");
        }

        if (event.cancelable) event.preventDefault();
        dx = mx;

        const next = here + (mx < 0 ? 1 : -1);
        const canGo = next >= 0 && next < sections.length;
        // Past either end the page gives a little and stops. Answering the
        // gesture beats ignoring it: the reader learns there is nothing there.
        const travel = canGo ? dx : dx * RESIST;

        if (main && !calm.matches) {
            // The page follows at a fraction of the finger: it is a hint that
            // something is being pulled, not a claim that the next page is
            // already loaded and sliding in.
            const shift = Math.max(-140, Math.min(140, travel * 0.45));
            main.style.transform = "translate3d(" + shift + "px, 0, 0)";
            main.style.opacity = String(1 - Math.min(Math.abs(shift) / 500, 0.3));
        }

        const wanted = canGo && Math.abs(mx) >= TRIGGER ? next : -1;
        if (wanted !== preview) {
            preview = wanted;
            moveBlob(wanted < 0 ? here : wanted);
        }
    }

    function onEnd() {
        if (!tracking) return;
        tracking = false;
        if (main) main.classList.remove("is-dragging");
        // Letting go is what the spring is for: the class goes now, the pill
        // settles back to size wherever it has landed.
        nav.classList.remove("is-swiping");

        const next = here + (dx < 0 ? 1 : -1);
        const commit = horizontal && preview === next;

        if (!commit) {
            if (main) {
                // Clearing the inline transform now that .is-dragging is gone
                // lets the spring in layout.css carry the page back instead of
                // snapping it there.
                main.style.transform = "";
                main.style.opacity = "";
            }
            moveBlob(here);
            return;
        }

        // The page leaves towards the section it is going to, and the section
        // being opened reads the same flag at the other end and enters from the
        // opposite side -- which is what makes one gesture out of two loads.
        if (main && !calm.matches) {
            main.classList.add(dx < 0 ? "is-swipe-out-next" : "is-swipe-out-prev");
        }
        try {
            window.sessionStorage.setItem(FLAG, dx < 0 ? "next" : "prev");
        } catch (error) {
            // No storage: the page still changes, it just does not animate in.
        }
        // Short enough to overlap the load: the fade is finishing while the
        // next page is already being fetched.
        window.setTimeout(() => window.location.assign(sections[next]), 170);
    }

    document.addEventListener("touchstart", onStart, { passive: true });
    // Not passive: a claimed horizontal gesture has to be able to stop the page
    // scrolling sideways under the finger.
    document.addEventListener("touchmove", onMove, { passive: false });
    document.addEventListener("touchend", onEnd, { passive: true });
    document.addEventListener("touchcancel", reset, { passive: true });

    /* The scroll edge ---------------------------------------------------------- */

    // On wide screens the sticky top bar starts as a pane lying on the lights
    // and turns into a lid over the content as soon as the page moves.
    const topnav = document.querySelector(".topnav");
    if (topnav) {
        const edge = () => topnav.classList.toggle("is-scrolled", window.scrollY > 8);
        window.addEventListener("scroll", edge, { passive: true });
        edge();
    }

    /* Coming back -------------------------------------------------------------- */

    // The blob has to be re-measured whenever the bar changes shape: a resize,
    // a rotation, or the webfont landing after the first paint and nudging the
    // labels a pixel wider.
    window.addEventListener("resize", () => moveBlob(here, true));
    window.addEventListener("orientationchange", () => moveBlob(here, true));
    window.addEventListener("load", () => moveBlob(here, true));
    if (document.fonts && document.fonts.ready) {
        document.fonts.ready.then(() => moveBlob(here, true));
    }

    // A page that was left mid-swipe is restored with the exit animation frozen
    // on it -- opacity and all. Putting it back is the whole job of this
    // listener; without it, going back leaves a dimmed, off-centre page.
    window.addEventListener("pageshow", (event) => {
        if (!event.persisted || !main) return;
        main.classList.remove(
            "is-dragging",
            "is-swipe-out-next",
            "is-swipe-out-prev",
            "nav-in-next",
            "nav-in-prev"
        );
        main.style.transform = "";
        main.style.opacity = "";
        moveBlob(here, true);
    });

    // Arriving page: it comes in from the side the finger left.
    if (arrived && main && !calm.matches) {
        main.classList.add(arrived === "next" ? "nav-in-next" : "nav-in-prev");
        main.addEventListener("animationend", () =>
            main.classList.remove("nav-in-next", "nav-in-prev")
        );
    }
})();
