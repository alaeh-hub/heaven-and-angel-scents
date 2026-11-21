// The mascot: a sprite-sheet character whose head turns toward the pointer and reacts
// to clicks. Vanilla port of the `page-mascot` React component (this app has no React).
// Nine head directions and nine expressions, each a 3x3 sheet swapped by moving
// `background-position` (no per-frame cost when idle).
// Both sheets live in static/mascots/ as a matched pair (same body pixels in all 18
// cells), so they swap on click without a visible jump.
(function () {
    'use strict';

    // Cell order within each sheet, left to right, top to bottom.
    const DIRECTIONS = ['up-left', 'up', 'up-right', 'left', 'center', 'right', 'down-left', 'down', 'down-right'];
    const REACTIONS = ['blink', 'heart', 'sparkle', 'surprised', 'wink', 'bashful', 'sleepy', 'dizzy', 'delighted'];

    // Clockwise from the right, matching atan2 with y pointing down.
    const CLOCKWISE = ['right', 'down-right', 'down', 'down-left', 'left', 'up-left', 'up', 'up-right'];
    const SECTOR = (Math.PI * 2) / CLOCKWISE.length;
    // Hold a sector slightly past its edge so the head doesn't flicker on a boundary.
    const HYSTERESIS = 0.12;
    // Within this many pixels of the centre there is no direction; look straight out.
    const DEAD_ZONE = 70;
    // How far the figure leans toward the pointer (fraction of size) and how fast it eases;
    // the lean makes the nine fixed head drawings feel fluid.
    const LEAN = 0.035;
    const LEAN_EASE = 0.16;
    // On touch there is no pointer: a tap is looked at briefly, then forgotten.
    const TAP_LOOK_MS = 1800;

    const BOOP_PAYOFF = 120;
    const BOOP_END = 560;
    const SQUASH_MS = 420;
    // Four clicks inside DIZZY_WINDOW and it gives up and spins.
    const DIZZY_AFTER = 4;
    const DIZZY_WINDOW = 1600;
    const DIZZY_END = 1100;

    // `tilt` is a few degrees away from the poke, so a click rocks it instead of squashing.
    function squashFrames(tilt) {
        return [
            { transform: 'rotate(0deg) scale(1, 1)', easing: 'ease-in' },
            { transform: 'rotate(' + (tilt * 0.6) + 'deg) scale(1.10, 0.86)', offset: 0.18, easing: 'ease-out' },
            { transform: 'rotate(' + (-tilt) + 'deg) scale(0.95, 1.08)', offset: 0.45, easing: 'ease-in-out' },
            { transform: 'rotate(' + (tilt * 0.35) + 'deg) scale(1.03, 0.97)', offset: 0.72, easing: 'ease-in-out' },
            { transform: 'rotate(0deg) scale(1, 1)' }
        ];
    }

    // Faces for the first three pokes (flinch then payoff); the fourth in a row is dizzy.
    const STAGES = [['blink', 'heart'], ['blink', 'sparkle'], ['surprised', 'delighted']];

    const BUBBLE_MS = 4800;

    const SHEETS = {
        directions: '/static/mascots/angel-directions.webp',
        reactions: '/static/mascots/angel-reactions.webp'
    };

    const FINE_POINTER = window.matchMedia('(hover: hover) and (pointer: fine)');
    const REDUCED_MOTION = window.matchMedia('(prefers-reduced-motion: reduce)');

    // Every mounted mascot; one pointermove listener drives all of them.
    const live = new Set();

    // background-size 300% makes each cell a clean 0/50/100% step on both axes.
    function cellPosition(index) {
        return ((index % 3) * 50) + '% ' + (Math.floor(index / 3) * 50) + '%';
    }

    function wrap(angle) {
        return Math.atan2(Math.sin(angle), Math.cos(angle));
    }

    /** "Good morning" / "Good afternoon" / "Good evening" for right now. */
    function timeGreeting() {
        const hour = new Date().getHours();
        if (hour < 12) return 'Good morning';
        if (hour < 18) return 'Good afternoon';
        return 'Good evening';
    }

    function Mascot(host, options) {
        const opts = options || {};
        const size = opts.size || 0;

        host.classList.add('mascot-host');
        if (size) {
            host.style.setProperty('--mascot-size', size + 'px');
            host.classList.add('mascot-host-sized');
        }

        const button = document.createElement('button');
        button.type = 'button';
        button.className = 'mascot';
        // An ornament: kept out of the tab order so keyboard users don't tab through it.
        button.tabIndex = -1;
        button.setAttribute('aria-hidden', 'true');

        const squash = document.createElement('span');
        squash.className = 'mascot-squash';

        const dirLayer = document.createElement('span');
        dirLayer.className = 'mascot-layer';
        dirLayer.style.backgroundImage = 'url("' + (opts.directions || SHEETS.directions) + '")';
        // Explicitly centred: the default 0% 0% is the up-left cell (looking away).
        dirLayer.style.backgroundPosition = cellPosition(DIRECTIONS.indexOf('center'));

        // Mounted from the start so the reactions sheet is fetched before the first poke.
        const reactLayer = document.createElement('span');
        reactLayer.className = 'mascot-layer mascot-layer-react';
        reactLayer.style.backgroundImage = 'url("' + (opts.reactions || SHEETS.reactions) + '")';
        reactLayer.style.backgroundPosition = cellPosition(0);

        squash.appendChild(dirLayer);
        squash.appendChild(reactLayer);
        button.appendChild(squash);
        host.appendChild(button);

        let bubble = null;
        let bubbleTimer = 0;

        const timers = [];
        const boops = { count: 0, at: 0 };
        let sector = -1;

        this.host = host;
        this.button = button;

        /** Point the head at `pointer`, or straight out when it's close. */
        let leanX = 0, leanY = 0, targetX = 0, targetY = 0, leanFrame = 0;

        function stepLean() {
            leanFrame = 0;
            leanX += (targetX - leanX) * LEAN_EASE;
            leanY += (targetY - leanY) * LEAN_EASE;
            if (Math.abs(targetX - leanX) < 0.02 && Math.abs(targetY - leanY) < 0.02) {
                leanX = targetX;
                leanY = targetY;
            } else {
                leanFrame = requestAnimationFrame(stepLean);
            }
            button.style.translate = leanX.toFixed(2) + 'px ' + leanY.toFixed(2) + 'px';
            button.style.rotate = (leanX * 0.5).toFixed(2) + 'deg';
        }

        function leanTo(x, y) {
            if (REDUCED_MOTION.matches) return;
            targetX = x;
            targetY = y;
            if (!leanFrame) leanFrame = requestAnimationFrame(stepLean);
        }

        this.aim = function (pointer) {
            // A mascot in a closed panel has a zero-size box; aiming from it is meaningless.
            const box = button.getBoundingClientRect();
            if (!box.width || !box.height) return;

            // Pointer left the window (or a tap faded): look straight out.
            if (!pointer) {
                leanTo(0, 0);
                if (sector === -1) return;
                sector = -1;
                dirLayer.style.backgroundPosition = cellPosition(DIRECTIONS.indexOf('center'));
                return;
            }

            const dx = pointer.x - (box.left + box.width / 2);
            const dy = pointer.y - (box.top + box.height / 2);

            const distance = Math.hypot(dx, dy);
            // Lean grows with distance and tops out.
            const pull = Math.min(1, distance / 240) * LEAN * box.width;
            leanTo(distance ? dx / distance * pull : 0, distance ? dy / distance * pull : 0);

            if (distance < Math.max(36, Math.min(DEAD_ZONE, box.width * 0.6))) {
                leanTo(0, 0);
                if (sector === -1) return;
                sector = -1;
                dirLayer.style.backgroundPosition = cellPosition(DIRECTIONS.indexOf('center'));
                return;
            }

            const angle = Math.atan2(dy, dx);
            // Hold the current sector until the pointer is well past its edge.
            if (sector !== -1 && Math.abs(wrap(angle - sector * SECTOR)) < SECTOR / 2 + HYSTERESIS) return;

            sector = (Math.round(angle / SECTOR) + CLOCKWISE.length) % CLOCKWISE.length;
            dirLayer.style.backgroundPosition = cellPosition(DIRECTIONS.indexOf(CLOCKWISE[sector]));
        };

        // Expression held until released (e.g. eyes shut while typing a password); timed
        // reactions fall back to it, not to the pointer.
        let held = null;

        function setReaction(name) {
            if (name === null && held) name = held;
            if (name === null) {
                host.classList.remove('is-reacting');
                return;
            }
            reactLayer.style.backgroundPosition = cellPosition(REACTIONS.indexOf(name));
            host.classList.add('is-reacting');
        }

        /** Put `text` in a speech bubble beside the mascot for a moment. */
        this.say = function (text, ms) {
            if (!text) return;
            if (!bubble) {
                bubble = document.createElement('span');
                bubble.className = 'mascot-bubble';
                host.appendChild(bubble);
            }
            window.clearTimeout(bubbleTimer);
            bubble.textContent = text;
            // Two frames: lay out the hidden state first, or the browser coalesces and nothing animates.
            bubble.classList.remove('is-shown');
            requestAnimationFrame(function () {
                requestAnimationFrame(function () {
                    bubble.classList.add('is-shown');
                });
            });
            const hold = ms === undefined ? BUBBLE_MS : ms;
            if (hold > 0) {
                bubbleTimer = window.setTimeout(function () {
                    bubble.classList.remove('is-shown');
                }, hold);
            }
        };

        /** Hold one named expression for `ms`, then follow the pointer again (e.g. wrong password). */
        this.react = function (name, ms) {
            if (REACTIONS.indexOf(name) === -1) return;
            timers.forEach(window.clearTimeout);
            timers.length = 0;
            setReaction(name);
            timers.push(window.setTimeout(function () { setReaction(null); }, ms || 1400));
        };

        /** Keep `name` showing until `cover(null)`. */
        this.cover = function (name) {
            if (name !== null && REACTIONS.indexOf(name) === -1) return;
            timers.forEach(window.clearTimeout);
            timers.length = 0;
            held = name;
            setReaction(name);
        };

        /** Dismiss the speech bubble now. */
        this.hush = function () {
            window.clearTimeout(bubbleTimer);
            if (bubble) bubble.classList.remove('is-shown');
        };

        this.boop = function (originX) {
            timers.forEach(window.clearTimeout);
            timers.length = 0;

            const later = function (ms, next) {
                timers.push(window.setTimeout(function () { setReaction(next); }, ms));
            };

            const now = Date.now();
            boops.count = now - boops.at < DIZZY_WINDOW ? boops.count + 1 : 1;
            boops.at = now;
            const count = boops.count;
            const dizzy = count >= DIZZY_AFTER;

            if (dizzy) {
                boops.count = 0;
                setReaction('dizzy');
                later(DIZZY_END, null);
            } else {
                const stage = STAGES[count - 1];
                setReaction(stage[0]);
                later(BOOP_PAYOFF, stage[1]);
                later(BOOP_END, null);
            }

            // Lets the page answer with a line that fits the poke count.
            host.dispatchEvent(new CustomEvent('mascot:boop', { detail: { count: count, dizzy: dizzy } }));

            if (REDUCED_MOTION.matches) return;
            const box = button.getBoundingClientRect();
            // Away from the poke; a keyboard or scripted boop rocks right.
            const side = originX === undefined || !box.width ? 1 : (originX < box.left + box.width / 2 ? 1 : -1);
            const tilt = side * (dizzy ? 9 : 4 + count);
            // Per-keyframe easing, effect linear: effect easing would reinterpret the offsets.
            if (squash.animate) squash.animate(squashFrames(tilt), { duration: dizzy ? SQUASH_MS * 1.6 : SQUASH_MS, easing: 'linear' });
        };

        // Pressed-in feedback the instant the pointer lands.
        const release = function () { host.classList.remove('is-pressed'); };
        button.addEventListener('pointerdown', function () { host.classList.add('is-pressed'); });
        ['pointerup', 'pointerleave', 'pointercancel'].forEach(function (name) {
            button.addEventListener(name, release);
        });

        const self = this;
        button.addEventListener('click', function (event) {
            // Halo's avatar sits beside real controls; a boop shouldn't trip the parent.
            event.preventDefault();
            event.stopPropagation();
            self.boop(event.clientX);
        });

        this.destroy = function () {
            timers.forEach(window.clearTimeout);
            window.clearTimeout(bubbleTimer);
            cancelAnimationFrame(leanFrame);
            live.delete(self);
            host.classList.remove('mascot-host', 'mascot-host-sized', 'is-reacting');
            button.remove();
            if (bubble) bubble.remove();
            delete host.dataset.mascotMounted;
        };

        live.add(this);
    }

    let pointer = null;

    function aimAll() {
        live.forEach(function (mascot) { mascot.aim(pointer); });
    }

    if (FINE_POINTER.matches) {
        window.addEventListener('pointermove', function (event) {
            pointer = { x: event.clientX, y: event.clientY };
            aimAll();
        }, { passive: true });
        // A still pointer over a scrolling page changes the angle too.
        window.addEventListener('scroll', function () { if (pointer) aimAll(); }, { passive: true });
        // Leaving the window leaves a stale position; face front instead.
        document.documentElement.addEventListener('mouseleave', function () {
            pointer = null;
            aimAll();
        });
    } else {
        // Touch: look at wherever the last tap landed, briefly.
        let tapTimer = 0;
        window.addEventListener('pointerdown', function (event) {
            pointer = { x: event.clientX, y: event.clientY };
            aimAll();
            window.clearTimeout(tapTimer);
            tapTimer = window.setTimeout(function () {
                pointer = null;
                aimAll();
            }, TAP_LOOK_MS);
        }, { passive: true });
    }

    /** Mount a mascot in `host`; idempotent, so a soft-nav re-init can't stack two. */
    function mount(host, options) {
        if (!host) return null;
        if (host.dataset.mascotMounted) return null;
        host.dataset.mascotMounted = '1';
        return new Mascot(host, options);
    }

    /**
     * Mount every [data-mascot], reading options off the element:
     *   <div data-mascot data-mascot-size="120" data-mascot-say="auto"></div>
     * say="auto" greets by time of day (data-mascot-say-name adds a name); other values
     * are said verbatim. data-mascot-replace clears the host first; the replaced markup
     * (Halo's glyph) stays in the template as the fallback if this script fails.
     */
    function initAll(root) {
        const scope = root || document;
        scope.querySelectorAll('[data-mascot]').forEach(function (host) {
            if (host.dataset.mascotReplace !== undefined && !host.dataset.mascotMounted) {
                host.textContent = '';
            }
            const size = parseInt(host.dataset.mascotSize, 10);
            const mascot = mount(host, { size: size > 0 ? size : 0 });
            if (!mascot) return;

            const say = host.dataset.mascotSay;
            if (!say) return;
            let text = say;
            if (say === 'auto') {
                const name = host.dataset.mascotSayName;
                text = timeGreeting() + (name ? ', ' + name : '');
            }
            // A beat after the page settles, so it reads as the mascot noticing you.
            window.setTimeout(function () { mascot.say(text); }, 600);
        });
    }

    window.Mascot = {
        mount: mount,
        initAll: initAll,
        timeGreeting: timeGreeting,
        sheets: SHEETS,
        /** The instance mounted in `host`, if there is one. */
        get: function (host) {
            let found = null;
            live.forEach(function (mascot) {
                if (mascot.host === host) found = mascot;
            });
            return found;
        }
    };
})();
