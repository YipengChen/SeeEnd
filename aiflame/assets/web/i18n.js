// Translate presentation text while retaining source strings for reversible switching.
let language = "zh-CN";
let catalog = {};
let phrases = null;
let phraseCatalog = {};
let templates = [];
let initialized = false;
let pendingLanguage = "zh-CN";
const sources = new WeakMap();
const escaped = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
export function translate(text) {
  if (language !== "en") return text;
  for (const {pattern, names, english} of templates) {
    const match = text.match(pattern);
    if (match) return english.replace(/\$\{([^}]+)\}/g, (_, name) => match[names.indexOf(name) + 1]);
  }
  return catalog[text] ?? text.replace(phrases, (match) => phraseCatalog[match]);
}
function sourceValue(node, key, current) {
  let record = sources.get(node);
  if (!record) { record = {}; sources.set(node, record); }
  if (!record[key] || current !== record[key].shown) record[key] = {source: current};
  const value = translate(record[key].source);
  record[key].shown = value;
  return value;
}
function renderLanguage() {
  observer.disconnect();
  document.documentElement.lang = language;
  const walker = document.createTreeWalker(document.documentElement, NodeFilter.SHOW_TEXT);
  while (walker.nextNode()) {
    const node = walker.currentNode;
    // Code, editable values, and user-provided network/device options are not UI copy.
    if (node.parentElement?.closest("script,style,pre,code,textarea,[data-user-content]")) continue;
    const value = sourceValue(node, "text", node.nodeValue);
    if (value !== node.nodeValue) node.nodeValue = value;
  }
  for (const node of document.querySelectorAll("[title],[placeholder],[aria-label]")) {
    for (const attribute of ["title", "placeholder", "aria-label"]) {
      if (node.hasAttribute(attribute)) {
        const current = node.getAttribute(attribute);
        const value = sourceValue(node, attribute, current);
        if (current !== value) node.setAttribute(attribute, value);
      }
    }
  }
  const selector = document.getElementById("languageChoice");
  if (selector) selector.value = language;
  observer.observe(document.documentElement, {subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ["title", "placeholder", "aria-label"]});
}
const observer = new MutationObserver(renderLanguage);
export function setLanguage(value) {
  if (!["zh-CN", "en"].includes(value)) return;
  pendingLanguage = value;
  if (!phrases) return;
  if (initialized && value === language) return;
  initialized = true;
  language = value;
  renderLanguage();
}
export async function initializeLanguage() {
  try {
    const response = await fetch("/locales/en.json");
    if (!response.ok) throw Error("Translation catalog unavailable");
    catalog = await response.json();
    templates = Object.entries(catalog).filter(([key]) => key.includes("${")).map(([key, english]) => {
      const names = [];
      const parts = key.split(/(\$\{[^}]+\})/g).map((part) => {
        if (part.startsWith("${")) { names.push(part.slice(2, -1)); return "(.+?)"; }
        return escaped(part);
      });
      return {pattern: new RegExp("^" + parts.join("") + "$"), names, english};
    });
    phraseCatalog = Object.fromEntries(Object.entries(catalog).map(([key, value]) => [key.trim(), value.trim()]));
    Object.assign(phraseCatalog, Object.fromEntries(Object.entries(catalog).filter(([key]) => key === key.trim())));
    phrases = new RegExp(Object.keys(phraseCatalog).filter((key) => key.length > 1 || ["秒", "台"].includes(key)).sort((a, b) => b.length - a.length).map(escaped).join("|"), "g");
    setLanguage(pendingLanguage);
    const responseStatus = await fetch("/api/status");
    if (responseStatus.ok) setLanguage((await responseStatus.json()).settings.language ?? "zh-CN");
    else renderLanguage();
    document.getElementById("languageChoice")?.addEventListener("change", async (event) => {
      const previous = language;
      const selector = event.target;
      const requested = selector.value;
      selector.disabled = true;
      try {
        const response = await fetch("/api/settings", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({language: requested})});
        if (!response.ok) throw Error((await response.json()).error || "Request failed");
        setLanguage(requested);
      } catch (error) {
        setLanguage(previous);
        selector.value = previous;
        const message = document.getElementById("message");
        if (message) message.textContent = error.message;
      } finally { selector.disabled = false; }
    });
  } catch (error) {
    console.error("SeeEnd language initialization failed", error);
  }
}
document.addEventListener("seeend-language", (event) => setLanguage(event.detail));
initializeLanguage();
