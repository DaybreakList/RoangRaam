(function () {
  'use strict';

  // Money and ratios arrive as decimal strings. Number() is used only to draw the bars.
  function toNumber(value) {
    return value === null ? null : Number(value);
  }

  function readJson(id) {
    var element = document.getElementById(id);
    return element ? JSON.parse(element.textContent) : null;
  }

  // Colours and the type face come from static/css/tokens.css, never from here, so the
  // charts move with the rest of the site. undefined falls back to the Chart.js default.
  var styles = getComputedStyle(document.documentElement);
  function token(name) {
    return styles.getPropertyValue(name).trim() || undefined;
  }
  var ink = token('--rr-color-primary');
  var accent = token('--rr-poster-accent');
  // 3.5:1 against the card: the axis and the grid stay visible on cream paper
  var line = token('--rr-color-input-border');
  var label = token('--rr-color-muted');
  var bodyFont = token('--rr-font-family');

  function drawBars(canvasId, labels, values, title, fill) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) {
      return;
    }
    new window.Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        // 2px ink edge on every bar, the same edge the cards and buttons carry
        datasets: [{
          label: title,
          data: values,
          backgroundColor: fill || ink,
          borderColor: ink,
          borderWidth: 2,
          borderRadius: 4,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        font: { family: bodyFont },
        plugins: { legend: { display: false } },
        scales: {
          y: {
            beginAtZero: true,
            border: { color: line },
            grid: { color: line, lineWidth: 1 },
            ticks: { color: label, font: { family: bodyFont } },
          },
          x: {
            border: { color: line },
            grid: { display: false },
            ticks: { color: label, font: { family: bodyFont } },
          },
        },
      },
    });
  }

  var revenue = readJson('revenue-data');
  if (revenue) {
    drawBars(
      'revenue-chart',
      revenue.map(function (day) { return day.date; }),
      revenue.map(function (day) { return toNumber(day.revenue); }),
      'รายได้ห้องพัก (บาท)'
    );
  }

  var hotels = readJson('compare-data');
  if (hotels) {
    var names = hotels.map(function (hotel) { return hotel.name; });
    // Two charts side by side: ink for Occupancy, accent for RevPAR, so a glance tells
    // them apart even before the title above each card is read.
    drawBars('occupancy-chart', names, hotels.map(function (hotel) { return toNumber(hotel.occupancy); }), 'Occupancy (%)');
    drawBars('revpar-chart', names, hotels.map(function (hotel) { return toNumber(hotel.revpar); }), 'RevPAR (บาท)', accent);
  }
})();
