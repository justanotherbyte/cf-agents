// Replace each `.cw-pypi-version` link's text with the package's latest
// version from PyPI. The link's default text ("PyPI") is the fallback, so a
// failed request, an unpublished package or disabled JS leaves it as is.

const PYPI_PROJECT = /^https:\/\/pypi\.org\/project\/([^/]+)\/?$/;

async function showLatestVersion(link) {
  const match = PYPI_PROJECT.exec(link.href);
  if (!match) return;

  try {
    const response = await fetch(`https://pypi.org/pypi/${match[1]}/json`);
    if (!response.ok) return;
    const { info } = await response.json();
    if (info?.version) link.textContent = info.version;
  } catch {
    // Network error or bad JSON: keep the PyPI fallback
  }
}

// Material's document$ re-emits on instant navigation; fall back to a plain
// page load if it isn't available
const render = () =>
  document.querySelectorAll("a.cw-pypi-version").forEach(showLatestVersion);

if (typeof document$ !== "undefined") {
  document$.subscribe(render);
} else {
  document.addEventListener("DOMContentLoaded", render);
}
