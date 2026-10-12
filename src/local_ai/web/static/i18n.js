// The page in two languages. English is written in the code; t() looks the Turkish text up by the English one.
// Text that comes from the agents (logs, tool output, verify results, reports) stays in the language it was written in.
"use strict";

const TR = {
  // top bar, tabs
  "Chats": "Sohbetler", "Projects": "Projeler", "Memory": "Bellek", "Skills": "Beceriler", "Commands": "Komutlar",
  "Commands and chats": "Komutlar ve sohbetler", "connecting…": "bağlanıyor…", "server not reachable": "sunucuya ulaşılamıyor",
  "model: auto": "model: otomatik", "no servers": "sunucu yok", "tools": "araç", "MCP server": "MCP sunucusu",
  "Drag to resize; double-click to reset": "Genişliği değiştirmek için sürükle; sıfırlamak için çift tıkla",
  "Theme": "Tema", "auto": "otomatik", "light": "açık", "dark": "koyu", "Language": "Dil",
  // composer
  "Research": "Araştır", "Code": "Kod", "Send": "Gönder", "Steer": "Yönlendir", "Cancel": "İptal", "Compact": "Sıkıştır",
  "Browse…": "Gözat…", "Waiting for the dialog…": "Pencere bekleniyor…", "Parallel": "Paralel", "Fast": "Hızlı", "Quick": "Kısa yanıt",
  "Skip review, gap check and learning": "İnceleme, eksik kontrolü ve öğrenme atlanır",
  "Quick answer: one tool loop, no plan or report": "Kısa yanıt: tek araç döngüsü, plan ve rapor yok",
  "Shorten the conversation before the next reply: the model writes notes, the plan stays": "Sonraki yanıttan önce konuşmayı kısalt: model not yazar, plan kalır",
  "Project folder, absolute path (e.g. C:\\Users\\you\\Github\\test\\app)": "Proje klasörü, tam yol (örn. C:\\Users\\sen\\Github\\test\\app)",
  "Project name (optional)": "Proje adı (isteğe bağlı)",
  "Ask a research question — any language.": "Bir araştırma sorusu sor.",
  "Ask a research question — any language. e.g. “Hidrojen atomunun taban durum enerjisini türet.”": "Bir araştırma sorusu sor. Örn. “Hidrojen atomunun taban durum enerjisini türet.”",
  "Ask a follow-up question.": "Takip sorusu sor.",
  "What to build, change or fix. Leave empty to continue the plan in the folder.": "Ne yazılsın, değişsin ya da düzeltilsin? Klasördeki plana devam etmek için boş bırak.",
  "Next instruction for this project. Leave empty to continue the plan.": "Bu proje için sıradaki talimat. Plana devam etmek için boş bırak.",
  "Tell the agent what to change. It reads the message before its next reply.": "Ajana neyi değiştireceğini yaz. Mesajı sonraki yanıtından önce okur.",
  "Research · follow-up in this chat": "Araştırma · bu sohbette takip sorusu",
  "New chat goes into folder: ": "Yeni sohbet şu klasöre girer: ",
  "Compaction requested: it happens before the next model reply.": "Sıkıştırma istendi: modelin sonraki yanıtından önce yapılır.",
  "What are we working on?": "Bugün ne üzerinde çalışıyoruz?",
  "Research answers a question from your library, Wikipedia and arXiv. Code writes and tests a project.": "Araştır, soruyu kütüphanenden, Wikipedia ve arXiv'den yanıtlar. Kod, bir projeyi yazar ve test eder.",
  "Ctrl+Enter sends.": "Ctrl+Enter gönderir.",
  // settings and meter
  "Model": "Model", "Temperature": "Sıcaklık", "Max tokens": "En çok token", "Max turns": "En çok tur", "Max steps": "En çok adım",
  "Defaults": "Varsayılanlar", "Default": "Varsayılan", "Default (first loaded model)": "Varsayılan (yüklü ilk model)",
  "Empty = default. Saved with this chat.": "Boş = varsayılan. Bu sohbetle birlikte saklanır.",
  "Higher is more varied, lower more exact": "Yüksek değer daha çeşitli, düşük değer daha kesin yanıt verir",
  "Tokens per model reply, thinking included": "Model yanıtı başına token, düşünme dahil",
  "Model replies in one run": "Bir çalıştırmadaki model yanıtı sayısı",
  "Context limit (characters)": "Bağlam sınırı (karakter)",
  "The conversation is compacted above this many characters (about 3 per token). Keep it below the context length set in LM Studio.":
    "Konuşma bu kadar karakteri aşınca sıkıştırılır (token başına yaklaşık 3 karakter). LM Studio'daki bağlam uzunluğunun altında tut.",
  "Tool-calling steps per sub-question": "Alt soru başına araç çağrısı adımı",
  "Model and settings of this chat": "Bu sohbetin modeli ve ayarları", "set": "ayar",
  "Turn": "Tur", "context": "bağlam", "tokens": "token", "chars": "karakter",
  "Context: {used}k of {limit}k characters": "Bağlam: {limit}k karakterin {used}k'sı",
  ". At 100% it is compacted: old messages are replaced by notes.": ". %100'de sıkıştırılır: eski mesajların yerini notlar alır.",
  // sidebar
  "+ New chat": "+ Yeni sohbet", "+ Folder": "+ Klasör", "Search chats  ( / )": "Sohbetlerde ara  ( / )", "Search chats": "Sohbetlerde ara",
  "Found": "Bulunanlar", "Pinned": "Sabitlenenler", "No folder": "Klasörsüz", "No chat matches.": "Eşleşen sohbet yok.",
  "Your chats appear here.": "Sohbetlerin burada görünür.", "Drop a chat here to take it out of its folder": "Sohbeti klasöründen çıkarmak için buraya bırak",
  "New chats go into this folder": "Yeni sohbetler bu klasöre girer", "Folder menu": "Klasör menüsü",
  "Rename, pin, move, delete": "Yeniden adlandır, sabitle, taşı, sil", "Rename": "Yeniden adlandır", "Unpin": "Sabitlemeyi kaldır",
  "Pin to the top": "Üste sabitle", "Move to folder…": "Klasöre taşı…", "New folder…": "Yeni klasör…", "Delete": "Sil",
  "Delete folder (chats stay)": "Klasörü sil (sohbetler kalır)", "Delete this chat?": "Bu sohbet silinsin mi?", "Chat deleted.": "Sohbet silindi.",
  "Delete the folder \"{name}\"? Its chats move out of it.": "\"{name}\" klasörü silinsin mi? İçindeki sohbetler dışarı taşınır.",
  "Chat title (empty = automatic)": "Sohbet başlığı (boş = otomatik)", "Folder name": "Klasör adı", "Name": "Ad",
  "code": "kod", "research": "araştırma", "queued": "sırada", "running": "çalışıyor", "done": "bitti", "stopped": "durdu",
  "failed": "başarısız", "cancelled": "iptal edildi", "interrupted": "yarıda kaldı", "open": "açık",
  "continue the plan": "plana devam et",
  // timeline
  "Tool calls": "Araç çağrıları", "Thinking": "Düşünme", "Model output": "Model çıktısı", "more lines": "satır daha",
  "Recalled from memory": "Bellekten hatırlananlar", "Gaps found — researching more": "Eksikler bulundu — araştırma sürüyor",
  "Reviewer": "İnceleyen", "Automatic checks": "Otomatik kontroller", "Reviewer found no issues.": "İnceleyen sorun bulmadı.",
  "Checks passed.": "Kontroller geçti.", "Computations notebook: ": "Hesaplama defteri: ", "cached tool calls": "araç çağrısı önbellekten",
  "cut off": "kesildi", "no tool call": "araç çağrısı yok", "run by the program": "programın çalıştırdığı",
  "Nothing to compact yet.": "Henüz sıkıştırılacak bir şey yok.",
  "Context compacted: {n} old messages replaced by the plan, the file list{notes}.": "Bağlam sıkıştırıldı: {n} eski mesajın yerini plan, dosya listesi{notes} aldı.",
  "Context compacted on request: {n} old messages replaced by the plan, the file list{notes}.": "Bağlam istek üzerine sıkıştırıldı: {n} eski mesajın yerini plan, dosya listesi{notes} aldı.",
  " and the model's notes": " ve modelin notları",
  "Finished: verification passes.": "Bitti: doğrulama geçiyor.", "Stopped": "Durdu", "Run": "Çalıştırma", "Export:": "Dışa aktar:",
  // progress panel, files
  "Plan": "Plan", "steps": "adım", "Acceptance criteria": "Kabul kriterleri", "Last verify": "Son doğrulama", "review": "inceleme",
  "Files changed": "Değişen dosyalar", "Sub-questions": "Alt sorular", "Sub-question": "Alt soru", "Sources": "Kaynaklar",
  "Project files": "Proje dosyaları", "Close": "Kapat", "This file cannot be shown as text.": "Bu dosya metin olarak gösterilemiyor.",
  // citations
  "Source": "Kaynak", "Unknown source": "Bilinmeyen kaynak", "Close (Esc)": "Kapat (Esc)",
  "No source with this number is registered.": "Bu numarayla kayıtlı kaynak yok.", "Click the number for the passage.": "Pasaj için numaraya tıkla.",
  "This quote was not found in the saved tool output.": "Bu alıntı kayıtlı araç çıktısında bulunamadı.",
  "No quote is registered for this source.": "Bu kaynak için kayıtlı alıntı yok.",
  // projects, memory, skills
  "Pick a project on the left.": "Soldan bir proje seç.", "No projects yet.": "Henüz proje yok.", "no report yet": "henüz rapor yok",
  "Loading…": "Yükleniyor…", "Notebook (.ipynb)": "Defter (.ipynb)", "Resume": "Devam et", "Findings": "Bulgular",
  "Search": "Ara", "Search memory (facts, lessons, preferences)": "Bellekte ara (bilgiler, dersler, tercihler)",
  "Forget": "Unut", "Forget this memory?": "Bu kayıt unutulsun mu?", "Nothing in memory yet.": "Bellekte henüz bir şey yok.",
  "Use when": "Ne zaman", "Add your own skills as Markdown files in": "Kendi becerilerini Markdown dosyası olarak şuraya ekle:",
  // command palette
  "New chat": "Yeni sohbet", "New chat in Research mode": "Araştırma modunda yeni sohbet", "New chat in Code mode": "Kod modunda yeni sohbet",
  "New folder": "Yeni klasör", "Cancel the run": "Çalıştırmayı iptal et", "Compact the conversation": "Konuşmayı sıkıştır",
  "Unpin this chat": "Bu sohbetin sabitlemesini kaldır", "Pin this chat": "Bu sohbeti sabitle", "Rename this chat": "Bu sohbeti yeniden adlandır",
  "Go to Projects": "Projelere git", "Go to Memory": "Belleğe git", "Go to Skills": "Becerilere git",
  "Switch the theme": "Temayı değiştir", "Switch the language to Turkish": "Dili Türkçe yap", "Switch the language to English": "Dili İngilizce yap",
  "Type a command or the name of a chat": "Bir komut ya da sohbet adı yaz", "Command": "Komut", "Nothing matches.": "Eşleşen yok.",
};

// The language chosen with the button of the top bar; before that, the browser's.
const LANG = (() => {
  try {
    const saved = localStorage.getItem("lang");
    if (saved === "tr" || saved === "en") return saved;
  } catch {}
  return (navigator.language || "").toLowerCase().startsWith("tr") ? "tr" : "en";
})();

function t(text, values) {
  const found = (LANG === "tr" && TR[text]) || text;
  return values ? found.replace(/\{(\w+)\}/g, (m, key) => values[key] ?? m) : found;
}

// The text written in index.html: text nodes, placeholders and tooltips that have a translation.
function translatePage(root) {
  if (LANG === "en") return;
  document.documentElement.lang = LANG;
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const text = node.nodeValue.trim();
    if (text && TR[text]) node.nodeValue = node.nodeValue.replace(text, TR[text]);
  }
  for (const node of root.querySelectorAll("[placeholder], [title], [aria-label]")) {
    for (const name of ["placeholder", "title", "aria-label"]) {
      const value = node.getAttribute(name);
      if (value && TR[value]) node.setAttribute(name, TR[value]);
    }
  }
}
