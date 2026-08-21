const HOST_MESSAGE_SCOPE = "ecomfans:funnel-template";
const COUNTDOWN_KEY = "north-haircare-demo-countdown";
const root = document.documentElement;
const body = document.body;
const editToggle = document.querySelector(".edit-toggle");
const saveButton = document.querySelector("[data-save]");
const resetButton = document.querySelector("[data-reset]");
const settingsButton = document.querySelector("[data-settings]");
const toast = document.querySelector(".toast");
const imageModal = document.querySelector("#image-editor");
const settingsModal = document.querySelector("#settings-editor");
const imagePreview = document.querySelector("[data-image-preview]");
const imageFile = document.querySelector("[data-image-file]");
const imageUrl = document.querySelector("[data-image-url]");
const imageAlt = document.querySelector("[data-image-alt]");
const ctaUrl = document.querySelector("[data-cta-url]");
const accentColor = document.querySelector("[data-accent-color]");
const colorValue = document.querySelector("[data-color-value]");

let activeImage = null;
let pendingImageSource = "";
let toastTimer;
let editorEnabled = false;

function sendHostMessage(action) {
  if (window.parent === window) return;
  window.parent.postMessage({ scope: HOST_MESSAGE_SCOPE, action }, "*");
}

function showToast(message) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("show");
  toastTimer = setTimeout(() => toast.classList.remove("show"), 2200);
}

function saveChanges() {
  if (!editorEnabled) return;
  setEditing(false);
  sendHostMessage("save");
  showToast("Saving changes…");
}

function notifyChange() {
  if (editorEnabled) sendHostMessage("change");
}

function setEditing(isEditing) {
  body.classList.toggle("is-editing", isEditing);
  document.querySelectorAll("[data-editable]").forEach((element) => {
    element.contentEditable = String(isEditing);
    element.spellcheck = isEditing;
  });
}

function openImageEditor(wrapper) {
  activeImage = wrapper;
  const image = wrapper.querySelector("img");
  pendingImageSource = image.src;
  imagePreview.src = image.src;
  imageUrl.value = image.src.startsWith("data:") ? "" : image.src;
  imageAlt.value = image.alt;
  imageFile.value = "";
  imageModal.showModal();
}

function shrinkRasterImage(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onerror = reject;
    reader.onload = () => {
      if (file.type === "image/svg+xml") {
        resolve(reader.result);
        return;
      }
      const tempImage = new Image();
      tempImage.onerror = reject;
      tempImage.onload = () => {
        const maxDimension = 1400;
        const scale = Math.min(1, maxDimension / Math.max(tempImage.width, tempImage.height));
        const canvas = document.createElement("canvas");
        canvas.width = Math.round(tempImage.width * scale);
        canvas.height = Math.round(tempImage.height * scale);
        canvas.getContext("2d").drawImage(tempImage, 0, 0, canvas.width, canvas.height);
        resolve(canvas.toDataURL("image/jpeg", 0.84));
      };
      tempImage.src = reader.result;
    };
    reader.readAsDataURL(file);
  });
}

editToggle.addEventListener("click", () => {
  setEditing(true);
  showToast("Edit mode is on - click any text or image");
});

saveButton.addEventListener("click", saveChanges);

resetButton.addEventListener("click", () => {
  if (!window.confirm("Reset all text, images, links, and colors to the original template?")) return;
  sendHostMessage("reset");
});

document.querySelectorAll("[data-editable]").forEach((element) => {
  element.addEventListener("input", notifyChange);
});

document.querySelectorAll(".image-edit-button").forEach((button) => {
  button.addEventListener("click", () => openImageEditor(button.closest("[data-image-key]")));
});

imageFile.addEventListener("change", async () => {
  const [file] = imageFile.files;
  if (!file) return;
  try {
    pendingImageSource = await shrinkRasterImage(file);
    imagePreview.src = pendingImageSource;
    imageUrl.value = "";
  } catch {
    showToast("We couldn't read that image file");
  }
});

imageUrl.addEventListener("input", () => {
  if (!imageUrl.value.trim()) return;
  pendingImageSource = imageUrl.value.trim();
  imagePreview.src = pendingImageSource;
});

document.querySelector("[data-apply-image]").addEventListener("click", (event) => {
  event.preventDefault();
  const source = imageUrl.value.trim() || pendingImageSource;
  if (!activeImage || !source) {
    showToast("Choose an image or paste an image URL first");
    return;
  }
  const image = activeImage.querySelector("img");
  image.src = source;
  image.alt = imageAlt.value.trim() || "Editorial image";
  imageModal.close();
  notifyChange();
  showToast("Image replaced - save when you're ready");
});

settingsButton.addEventListener("click", () => {
  ctaUrl.value = document.querySelector("[data-cta-link]")?.href || "";
  const currentAccent = getComputedStyle(root).getPropertyValue("--accent").trim();
  accentColor.value = rgbToHex(currentAccent) || "#f6c72c";
  colorValue.textContent = accentColor.value.toUpperCase();
  settingsModal.showModal();
});

accentColor.addEventListener("input", () => {
  colorValue.textContent = accentColor.value.toUpperCase();
});

document.querySelector("[data-apply-settings]").addEventListener("click", (event) => {
  event.preventDefault();
  const newUrl = ctaUrl.value.trim();
  if (newUrl) document.querySelectorAll("[data-cta-link]").forEach((link) => (link.href = newUrl));
  root.style.setProperty("--accent", accentColor.value);
  settingsModal.close();
  notifyChange();
  showToast("Page settings applied - save when you're ready");
});

document.querySelectorAll(".shade-picker button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll(".shade-picker button").forEach((item) => item.classList.remove("selected"));
    button.classList.add("selected");
    notifyChange();
  });
});

document.querySelectorAll("[data-cta-link]").forEach((link) => {
  link.addEventListener("click", (event) => {
    if (body.classList.contains("is-editing")) {
      event.preventDefault();
      showToast("Finish editing and save before opening the offer link");
    }
  });
});

function rgbToHex(color) {
  if (color.startsWith("#")) return color;
  const values = color.match(/\d+/g);
  if (!values || values.length < 3) return "";
  return `#${values.slice(0, 3).map((value) => Number(value).toString(16).padStart(2, "0")).join("")}`;
}

function startDemoCountdown() {
  let endTime = Number(sessionStorage.getItem(COUNTDOWN_KEY));
  if (!endTime || endTime <= Date.now()) {
    endTime = Date.now() + 12 * 60 * 60 * 1000;
    sessionStorage.setItem(COUNTDOWN_KEY, String(endTime));
  }

  const render = () => {
    const remaining = Math.max(0, endTime - Date.now());
    const totalSeconds = Math.floor(remaining / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    document.querySelector("[data-countdown-hours]").textContent = String(hours).padStart(2, "0");
    document.querySelector("[data-countdown-minutes]").textContent = String(minutes).padStart(2, "0");
    document.querySelector("[data-countdown-seconds]").textContent = String(seconds).padStart(2, "0");
  };

  render();
  window.setInterval(render, 1000);
}

window.addEventListener("message", (event) => {
  if (event.source !== window.parent || event.data?.scope !== HOST_MESSAGE_SCOPE) return;
  if (event.data.action === "enable") {
    editorEnabled = true;
    body.classList.add("template-editor-enabled");
    return;
  }
  if (event.data.action === "saved") {
    showToast("Changes saved to your funnel");
    return;
  }
  if (event.data.action === "save-error") {
    showToast(event.data.message || "The page could not be saved");
  }
});

document.querySelector("#current-year").textContent = new Date().getFullYear();
startDemoCountdown();
sendHostMessage("ready");
