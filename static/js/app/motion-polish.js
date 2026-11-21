// Two small Motion (motion.dev) additions for the back office (loaded by _app_scripts.html).
// Transform/opacity only, skipped under prefers-reduced-motion, and nothing is hidden
// unless it is about to animate in, so no-JS or missing Motion leaves the page unchanged.

var FX_EASE = [0.22, 1, 0.36, 1];

// Cards below the fold lift in once when first scrolled into view; on-screen cards are
// left to the page reveal. Re-runs after a real soft navigation.
function initBelowFoldReveal() {
    var M = window.Motion;
    if (!M || typeof M.inView !== 'function' || PREFERS_REDUCED_MOTION.matches) return;
    var content = document.querySelector('.content');
    if (!content) return;

    var viewport = window.innerHeight;
    var targets = Array.prototype.filter.call(content.querySelectorAll('.card'), function (el) {
        if (el.dataset.fxDone || el.offsetParent === null) return false;
        if (el.parentElement && el.parentElement.closest('.card')) return false; // the outer card only
        if (el.closest('[hidden], dialog, [role="dialog"], .modal')) return false;
        return el.getBoundingClientRect().top > viewport * 0.92;
    }).slice(0, 20);

    targets.forEach(function (el) {
        el.dataset.fxDone = '1';
        el.style.opacity = '0';
        el.style.transform = 'translateY(18px)';
        // Returning nothing from the callback makes inView fire once per element.
        M.inView(el, function () {
            // Motion leaves the last keyframe inline; clear it so the card's CSS owns transform again.
            var clear = function () {
                window.setTimeout(function () {
                    el.style.opacity = '';
                    el.style.transform = '';
                }, 0);
            };
            var anim = M.animate(
                el,
                { opacity: [0, 1], transform: ['translateY(18px)', 'translateY(0px)'] },
                { duration: 0.5, easing: FX_EASE }
            );
            if (anim && typeof anim.then === 'function') anim.then(clear, clear);
            else clear();
        }, { margin: '0px 0px -8% 0px' });
    });
}

// An invalid field shakes sideways on submit. One capture-phase document listener
// (`invalid` doesn't bubble), so it covers soft-nav content too.
function initFieldShake() {
    if (window.__fieldShakeBound) return;
    window.__fieldShakeBound = true;
    document.addEventListener('invalid', function (event) {
        var M = window.Motion;
        var field = event.target;
        if (!M || PREFERS_REDUCED_MOTION.matches || !field || field.dataset.fxShaking) return;
        field.dataset.fxShaking = '1';
        var reset = function () {
            delete field.dataset.fxShaking;
            // After Motion has committed its final keyframe (see the card reveal above).
            window.setTimeout(function () { field.style.transform = ''; }, 0);
        };
        var anim = M.animate(
            field,
            { transform: ['translateX(0px)', 'translateX(-6px)', 'translateX(5px)', 'translateX(-3px)', 'translateX(0px)'] },
            { duration: 0.38, easing: 'ease-out' }
        );
        if (anim && typeof anim.then === 'function') anim.then(reset, reset);
        else reset();
    }, true);
}
