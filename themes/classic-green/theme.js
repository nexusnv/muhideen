// Read-only consumer of the contract. Polls next-event; SSE upgrades when present.
async function tick() {
  try {
    const r = await fetch('/api/next-event', {cache: 'no-store'});
    const e = await r.json();
    document.getElementById('next').textContent = e.state + ' → ' + (e.next_prayer || '');
  } catch { /* keep last rendered state offline */ }
}
setInterval(tick, 5000); tick();
