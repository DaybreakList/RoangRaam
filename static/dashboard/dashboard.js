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

  var styles = getComputedStyle(document.documentElement);
  var primary = styles.getPropertyValue('--rr-color-primary').trim() || '#1f6f5c';

  function drawBars(canvasId, labels, values, title) {
    var canvas = document.getElementById(canvasId);
    if (!canvas || !window.Chart) {
      return;
    }
    new window.Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{ label: title, data: values, backgroundColor: primary }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: { y: { beginAtZero: true } },
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
    drawBars('occupancy-chart', names, hotels.map(function (hotel) { return toNumber(hotel.occupancy); }), 'Occupancy (%)');
    drawBars('revpar-chart', names, hotels.map(function (hotel) { return toNumber(hotel.revpar); }), 'RevPAR (บาท)');
  }
})();
