/* Measurement fixture only; never loaded by the application templates. */
(() => {
    const measurements = {readyMs: null, frames: [], longTasks: [], swaps: []};
    let previous, swapStart;
    const summarize = () => {
        const frames = [...measurements.frames].sort((a, b) => a - b);
        const payload = JSON.parse(document.querySelector('#tc-move-data')?.textContent || '{}');
        return {...measurements, frames: undefined, frameCount: frames.length,
            frameP95: frames[Math.ceil(frames.length * .95) - 1] || 0,
            frameMax: frames.at(-1) || 0,
            nodes: document.querySelectorAll('*').length,
            bars: document.querySelectorAll('.tc-bar').length,
            rows: document.querySelectorAll('.tc-row').length,
            totalBars: Object.keys(payload.stays || {}).length,
            totalRooms: Object.keys(payload.rooms || {}).length};
    };
    const show = () => {
        let output = document.getElementById('benchmark-results');
        if (!output) {
            output = document.createElement('pre');
            output.id = 'benchmark-results';
            output.style.cssText = 'white-space:pre-wrap;font-size:11px';
            document.body.appendChild(output);
        }
        output.textContent = JSON.stringify(summarize());
    };
    try { new PerformanceObserver(list => {
        for (const entry of list.getEntries()) measurements.longTasks.push({start: entry.startTime, ms: entry.duration});
    }).observe({type: 'longtask', buffered: true}); } catch (_) { /* Unsupported engine. */ }
    document.addEventListener('alpine:initialized', () => requestAnimationFrame(() => requestAnimationFrame(() => {
        measurements.readyMs = performance.now(); show();
    })));
    document.addEventListener('htmx:beforeRequest', () => { swapStart = performance.now(); });
    document.addEventListener('htmx:afterSettle', () => requestAnimationFrame(() => requestAnimationFrame(() => {
        measurements.swaps.push(performance.now() - swapStart); show();
    })));
    function frame(now) {
        if (previous && document.querySelector('.tc-drag-ghost')?.getBoundingClientRect().width) {
            measurements.frames.push(now - previous);
        }
        previous = now;
        requestAnimationFrame(frame);
    }
    requestAnimationFrame(frame);
    document.addEventListener('pointerup', () => setTimeout(show, 500));
})();
