/* Service worker do PlayGo: guarda o "casco" do app para abrir rápido e funcionar sem rede até a
   tela de aviso. Dados (/api) nunca são guardados: vaga em tempo real não pode ser vaga velha. */
const VERSAO = 'playgo-app-v82';
const CASCO = ['./', 'index.html', 'app.css', 'app.js', 'manifest.json', 'icone-192.png', 'icone-512.png', 'apple-touch-icon-novo.png'];

self.addEventListener('install', (e) => {
  e.waitUntil(caches.open(VERSAO).then((c) => c.addAll(CASCO)).then(() => self.skipWaiting()));
});

self.addEventListener('activate', (e) => {
  e.waitUntil(caches.keys().then((chaves) => Promise.all(chaves.filter((k) => k !== VERSAO).map((k) => caches.delete(k)))).then(() => self.clients.claim()));
});

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== 'GET' || url.origin !== location.origin || url.pathname.startsWith('/api/')) return;
  // Rede primeiro (para pegar versões novas); cai para o cache sem conexão
  e.respondWith(
    fetch(e.request)
      .then((r) => {
        const copia = r.clone();
        caches.open(VERSAO).then((c) => c.put(e.request, copia));
        return r;
      })
      .catch(() => caches.match(e.request).then((r) => r || caches.match('index.html')))
  );
});

// ---- Avisos no celular (Web Push). O servidor manda {titulo, corpo, link, tag}; aqui vira a notificação com a logo do PlayGo.
// icon = logo colorida (grande, dentro do aviso); badge = silhueta branca da bola (pequena, na barra de status do Android).
self.addEventListener('push', (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch (err) { d = { corpo: e.data ? e.data.text() : '' }; }
  e.waitUntil(self.registration.showNotification(d.titulo || 'PlayGo', {
    body: d.corpo || '', icon: '/app/icone-192.png', badge: '/app/badge-push-96.png', tag: d.tag || undefined, lang: 'pt-BR',
    data: { link: d.link || '/notificacoes' },
  }));
});

self.addEventListener('notificationclick', (e) => {
  e.notification.close();
  const alvo = new URL('/app/#' + ((e.notification.data && e.notification.data.link) || '/notificacoes'), self.location.origin).href;
  e.waitUntil(self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((janelas) => {
    const aberta = janelas.find((j) => j.url.startsWith(self.location.origin + '/app/'));
    if (aberta) return aberta.focus().then(() => (aberta.navigate ? aberta.navigate(alvo) : null)).catch(() => self.clients.openWindow(alvo));
    return self.clients.openWindow(alvo);
  }));
});
