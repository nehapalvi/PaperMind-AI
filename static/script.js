const API_BASE = "http://localhost:8000";

const chatBox = document.getElementById("chatBox");
const questionInput = document.getElementById("question");
const sendBtn = document.querySelector(".send-btn");


/* =========================
   PDF UPLOAD
========================= */
async function uploadPDF() {
    const fileInput = document.getElementById("pdfUpload");

    if (!fileInput.files.length) {
        addMessage("Please select a PDF file first.", "bot");
        return;
    }

    const formData = new FormData();
    formData.append("file", fileInput.files[0]);

    addMessage("Uploading and processing PDF...", "bot");

    try {
        const response = await fetch(`${API_BASE}/upload`, {
            method: "POST",
            body: formData
        });

        if (!response.ok) throw new Error("Upload failed");

        addMessage("✅ PDF processed successfully! You can now ask questions.", "bot");

    } catch (error) {
        console.error("Upload error:", error);
        addMessage("❌ Error uploading PDF. Check backend server.", "bot");
    }
}

/* =========================
   SEND MESSAGE
========================= */
async function sendMessage() {
    const question = questionInput.value.trim();
    const level = document.getElementById("level").value;

    if (!question) return;

    addMessage(question, "user");
    questionInput.value = "";

    setLoading(true);
    const typingIndicator = addTypingIndicator();

    try {
        const response = await fetch(`${API_BASE}/ask`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ question, level })
        });

        if (!response.ok) throw new Error("Server error");

        const data = await response.json();
        removeTypingIndicator(typingIndicator);

        // Extract the structured answer
        const answer = data.answer || {};

        let html = "";

        // Main Idea
        if (answer.main_idea) {
            html += `
                <div class="section">
                    <strong>📌 Main Idea</strong>
                    <p>${answer.main_idea}</p>
                </div>
            `;
        }

        // Key Concepts
        if (answer.key_concepts && Array.isArray(answer.key_concepts)) {
            const conceptsHTML = answer.key_concepts.map(c => {
                if (typeof c === "string") {
                    return `<li>${c}</li>`;
                } else {
                    return `<li><strong>${c.concept || c.term || "N/A"}:</strong> ${c.explanation || "Not available"}</li>`;
                }
            }).join("");
            html += `
                <div class="section">
                    <strong>💡 Key Concepts</strong>
                    <ul>${conceptsHTML}</ul>
                </div>
            `;
        }

        // Equations Explained
        if (answer.equations_explained) {
            html += `
                <div class="section">
                    <strong>🧮 Equations Explained</strong>
                    <p>${answer.equations_explained}</p>
                </div>
            `;
        }

        // Real World Example
        if (answer.real_world_example) {
            html += `
                <div class="section">
                    <strong>🌎 Real World Example</strong>
                    <p>${answer.real_world_example}</p>
                </div>
            `;
        }

        // Simple Summary
        if (answer.simple_summary) {
            html += `
                <div class="section">
                    <strong>📝 Simple Summary</strong>
                    <p>${answer.simple_summary}</p>
                </div>
            `;
        }

        // Fallback if nothing is present or response is raw
        if (!html) {
            html = `<pre>${answer.raw_response ? answer.raw_response : JSON.stringify(answer, null, 2)}</pre>`;
        }

        addMessage(html, "bot", true);

    } catch (error) {
        removeTypingIndicator(typingIndicator);
        addMessage("❌ Error getting response. Make sure backend is running.", "bot");
        console.error(error);
    }

    setLoading(false);
}

/* =========================
   FORMAT BOT RESPONSE
========================= */
function addFormattedResponse(data) {

    let html = "";

    if (data.main_idea) {
        html += `
            <div class="section">
                <strong>📌 Main Idea</strong>
                <p>${data.main_idea}</p>
            </div>
        `;
    }

    if (data.key_concepts && Array.isArray(data.key_concepts)) {
        html += `
            <div class="section">
                <strong>📚 Key Concepts</strong>
                <ul>
                    ${data.key_concepts.map(concept => `<li>${concept}</li>`).join("")}
                </ul>
            </div>
        `;
    }

    if (data.simple_summary) {
        html += `
            <div class="section">
                <strong>📝 Simple Summary</strong>
                <p>${data.simple_summary}</p>
            </div>
        `;
    }

    if (data.real_world_example) {
        html += `
            <div class="section">
                <strong>🌍 Real World Example</strong>
                <p>${data.real_world_example}</p>
            </div>
        `;
    }

    if (data.equations_explained) {
        html += `
            <div class="section">
                <strong>📐 Equations Explained</strong>
                <p>${data.equations_explained}</p>
            </div>
        `;
    }

    // Fallback if unexpected format
    if (!html) {
        html = `<pre>${JSON.stringify(data, null, 2)}</pre>`;
    }

    addMessage(html, "bot", true);
}


/* =========================
   MESSAGE HANDLING
========================= */
function addMessage(content, type, isHTML = false) {
    const div = document.createElement("div");
    div.className = "message " + type;

    if (isHTML) {
        div.innerHTML = content;
    } else {
        div.innerText = content;
    }

    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;

    return div;
}

function addTypingIndicator() {
    const div = document.createElement("div");
    div.className = "message bot typing";
    div.innerText = "Typing...";
    chatBox.appendChild(div);
    chatBox.scrollTop = chatBox.scrollHeight;
    return div;
}

function removeTypingIndicator(element) {
    if (element) element.remove();
}


/* =========================
   UI HELPERS
========================= */
function setLoading(isLoading) {
    sendBtn.disabled = isLoading;
    sendBtn.style.opacity = isLoading ? "0.6" : "1";
}


/* =========================
   ENTER KEY SUPPORT
========================= */
questionInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") {
        e.preventDefault();
        sendMessage();
    }
});