// Basic Service Worker
// Currently empty but defined to avoid registration errors

self.addEventListener('install', (event) => {
    // console.log('SW installed');
    self.skipWaiting();
});

self.addEventListener('activate', (event) => {
    // console.log('SW activated');
});

// self.addEventListener('fetch', (event) => {
//     // For now, don't intercept anything
// });
