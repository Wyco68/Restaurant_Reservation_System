/*
 * Runtime settings, read by src/api.js.
 *
 * This file is for local development (python -m http.server). The web
 * image serves its own /config.js, written from its environment at start.
 */
window.TABLEFLOW_CONFIG = { apiBase: "http://localhost:8000" };
