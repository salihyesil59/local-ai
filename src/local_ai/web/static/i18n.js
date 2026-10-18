// The page in two languages. English is written in the code; t() looks the Turkish text up by the English one.
// Text that comes from the agents (logs, tool output, verify results, reports) stays in the language it was written in.
"use strict";

const TR = {
  // top bar, tabs
  "Chats": "Sohbetler", "Projects": "Projeler", "Memory": "Bellek", "Skills": "Beceriler", "Commands": "Komutlar",
  "Commands and chats": "Komutlar ve sohbetler", "connecting…": "bağlanıyor…", "server not reachable": "sunucuya ulaşılamıyor",
  "model: auto": "model: otomatik", "no servers": "sunucu yok", "tools": "araç", "MCP server": "MCP sunucusu",
  "Drag to resize; double-click to reset": "Genişliği değiştirmek için sürükle; sıfırlamak için çift tıkla",
  "Attach images (or paste / drop them)": "Görsel ekle (ya da yapıştır / sürükle bırak)", "Attach images": "Görsel ekle",
  "Attached image": "Eklenen görsel", "Only PNG, JPEG, WebP or GIF images can be attached.": "Yalnızca PNG, JPEG, WebP ya da GIF görseller eklenebilir.",
  "At most 6 images per message.": "Bir mesajda en çok 6 görsel olabilir.",
  "Short thinking": "Kısa düşünme", "Depth": "Derinlik", "Off": "Kapalı", "Plan review": "Plan onayı",
  "Deepening: after each step the findings are reflected on and what they opened is followed":
    "Derinleştirme: her adımdan sonra bulgular üzerine düşünülür ve açtıkları yeni sorular izlenir",
  "Clarify and approve the plan": "Netleştir ve planı onayla",
  "Asks a few questions first if needed, then shows the plan: change it and start the research":
    "Gerekirse önce birkaç soru sorar, sonra planı gösterir: düzenle ve araştırmayı başlat",
  "Before researching": "Araştırmadan önce",
  "Answer in the message box; the research starts from your answers.": "Mesaj kutusundan yanıtla; araştırma yanıtlarınla başlar.",
  "The plan: change it, then start the research": "Plan: düzenle, sonra araştırmayı başlat",
  "Sub-question": "Alt soru", "+ Add a sub-question": "+ Alt soru ekle", "Approve and start": "Onayla ve başla",
  "Keep at least one sub-question.": "En az bir alt soru kalmalı.",
  "Each step thinks briefly and then acts: much faster, a little less careful":
    "Her adım kısa düşünüp harekete geçer: çok daha hızlı, biraz daha az titiz",
  "Attach files": "Dosya ekle", "Click to see all of the thinking": "Düşünmenin tamamını görmek için tıkla",
  "Attach images and files: PDF, Word, PowerPoint, Excel, notes, code (or paste / drop them)":
    "Görsel ve dosya ekle: PDF, Word, PowerPoint, Excel, not, kod (ya da yapıştır / sürükle bırak)",
  "{name}: this kind of file cannot be attached.": "{name}: bu tür dosya eklenemez.",
  "At most 10 files per message.": "Bir mesajda en çok 10 dosya olabilir.",
  "{name} is empty or larger than 50 MB.": "{name} boş ya da 50 MB'tan büyük.",
  "always on": "hep açık", "Chat": "Sohbet", "Send now": "Şimdi gönder",
  "Stop the current work and answer this message now": "Şu anki işi durdur ve bu mesajı hemen yanıtla",
  "Write your next message: it is answered when the current reply is done.": "Sonraki mesajını yaz: şu anki yanıt bitince cevaplanır.",
  "Write your next question: it is researched when this research is done.": "Sonraki sorunu yaz: bu araştırma bitince araştırılır.",
  "Offline": "Çevrimdışı", "Notebooks": "Defterler", "Notebook": "Defter", "No notebook": "Defter yok", "Library": "Kütüphane",
  "ZIM archives": "ZIM arşivleri", "Create": "Oluştur", "Name of a new notebook": "Yeni defterin adı", "Add files": "Dosya ekle",
  "files": "dosya", "Folder": "Klasör", "Folders": "Klasörler", "indexed": "indekslendi", "new": "yeni",
  "In the search index": "Arama indeksinde", "Indexed on the next search": "Bir sonraki aramada indekslenir",
  "No notebooks yet.": "Henüz defter yok.", "No files yet.": "Henüz dosya yok.", "Drop files here or use Add files.": "Dosyaları buraya bırak ya da Dosya ekle'yi kullan.",
  "No ZIM archives in these folders.": "Bu klasörlerde ZIM arşivi yok.", "Check for updates": "Güncellemeleri denetle",
  "Asking the Kiwix catalog…": "Kiwix kataloğuna soruluyor…", "All archives are up to date.": "Tüm arşivler güncel.",
  "Download the updates": "Güncellemeleri indir", "Keep the old files": "Eski dosyaları tut", "Downloading": "İndiriliyor",
  "Added: {name}": "Eklendi: {name}", "Notebook created: {name}": "Defter oluşturuldu: {name}", "created with the first file": "ilk dosyayla oluşturulur",
  "Delete \"{name}\" from the library?": "\"{name}\" kütüphaneden silinsin mi?",
  "Delete the notebook \"{name}\" and its {n} files?": "\"{name}\" defteri ve içindeki {n} dosya silinsin mi?",
  "{n} more files not shown": "{n} dosya daha gösterilmiyor",
  "Needs the internet: newer releases come from the Kiwix catalog.": "İnternet gerekir: yeni sürümler Kiwix kataloğundan gelir.",
  "What the agents can read without the internet: notebooks, your library and the ZIM archives.": "Ajanların internetsiz okuyabildikleri: defterler, kütüphanen ve ZIM arşivleri.",
  "A notebook is a handful of files for one topic. Choose it at the message box of a chat or a research: the model then searches only its files, not the whole library.":
    "Defter, tek bir konu için birkaç dosyadır. Sohbet ya da araştırmada mesaj kutusundan seç: model o zaman bütün kütüphaneyi değil yalnızca bu dosyaları arar.",
  "Drop PDF, Markdown or text files here, or": "PDF, Markdown ya da metin dosyalarını buraya bırak ya da", "choose files": "dosya seç",
  "They are copied into the library and indexed on the next search.": "Kütüphaneye kopyalanır ve bir sonraki aramada indekslenir.",
  "A notebook: this message searches only its files": "Defter: bu mesaj yalnızca onun dosyalarında arar",
  "Write a message — any language. Images can be pasted or dropped here.": "Bir mesaj yaz — herhangi bir dilde. Görseller buraya yapıştırılabilir ya da sürüklenebilir.",
  "Plan and verify, but no review at the end and no project map; shorter thinking":
    "Plan ve doğrulama var; sondaki inceleme ve proje haritası yok, düşünme daha kısa",
  "A small mechanical change (a rename): no plan, the tests run once at the end":
    "Küçük, mekanik bir değişiklik (yeniden adlandırma gibi): plan yok, testler sonda bir kez çalışır",
  "The research agent keeps its plans, findings, memory and reports in this server, so it cannot be switched off or removed.":
    "Araştırma ajanı planlarını, bulgularını, belleğini ve raporlarını bu sunucuda tutar; bu yüzden kapatılamaz ve kaldırılamaz.",
  "Tools": "Araçlar", "Servers this message may use": "Bu mesajın kullanabileceği sunucular", "No servers are running.": "Çalışan sunucu yok.",
  "Manage servers…": "Sunucuları yönet…", "The MCP servers this message may use": "Bu mesajın kullanabileceği MCP sunucuları",
  "MCP servers give the agents their tools. Add one you wrote or downloaded; it starts at once and is saved in the server list.":
    "MCP sunucuları ajanlara araçlarını verir. Kendi yazdığın ya da indirdiğin bir sunucuyu ekle; hemen başlar ve sunucu listesine kaydedilir.",
  "Add a server": "Sunucu ekle", "Command or URL": "Komut ya da URL", "Paste JSON": "JSON yapıştır", "Name": "Ad",
  "Environment (optional, one KEY=value per line)": "Ortam değişkenleri (isteğe bağlı, her satıra bir ANAHTAR=değer)",
  "The JSON from the server's README": "Sunucunun README dosyasındaki JSON", "Add and start": "Ekle ve başlat",
  "A server is a program that runs on this computer with your rights: add only what you trust.":
    "Sunucu, bu bilgisayarda senin yetkilerinle çalışan bir programdır: yalnızca güvendiğini ekle.",
  "Saved in": "Kayıt yeri:", "This UI was started without a server list to change.": "Bu arayüz değiştirilebilir bir sunucu listesi olmadan başlatıldı.",
  "switched off": "kapalı", "not running": "çalışmıyor", "On or off": "Aç ya da kapat", "Restart": "Yeniden başlat", "Remove": "Kaldır",
  "Remove the server \"{name}\" from the list?": "\"{name}\" sunucusu listeden kaldırılsın mı?", "No servers yet.": "Henüz sunucu yok.",
  "\"{name}\" is running with {n} tools.": "\"{name}\" {n} araçla çalışıyor.",
  "\"{name}\" was saved but did not start: {error}": "\"{name}\" kaydedildi ama başlamadı: {error}",
  "Go to Tools (MCP servers)": "Araçlara git (MCP sunucuları)", "Project name": "Proje adı",
  "Theme": "Tema", "auto": "otomatik", "light": "açık", "dark": "koyu", "Language": "Dil",
  // composer
  "Research": "Araştır", "Code": "Kod", "Send": "Gönder", "Steer": "Yönlendir", "Stop": "Durdur", "Cancel": "İptal", "Compact": "Sıkıştır",
  "Browse…": "Gözat…", "Waiting for the dialog…": "Pencere bekleniyor…", "Parallel": "Paralel", "Fast": "Hızlı", "Quick": "Kısa yanıt",
  "Skip review, gap check and learning": "İnceleme, eksik kontrolü ve öğrenme atlanır",
  "Quick answer: one tool loop, no plan or report": "Kısa yanıt: tek araç döngüsü, plan ve rapor yok",
  "Shorten the conversation before the next reply: the model writes notes, the plan stays": "Sonraki yanıttan önce konuşmayı kısalt: model not yazar, plan kalır",
  "Project folder, absolute path (e.g. C:\\Users\\you\\Github\\test\\app)": "Proje klasörü, tam yol (örn. C:\\Users\\sen\\Github\\test\\app)",
  "Project name (optional)": "Proje adı (isteğe bağlı)",
  "Ask a research question — any language.": "Bir araştırma sorusu sor.",
  "Ask a research question — any language. e.g. “Hidrojen atomunun taban durum enerjisini türet.”": "Bir araştırma sorusu sor. Örn. “Hidrojen atomunun taban durum enerjisini türet.”",
  "Ask a follow-up question.": "Takip sorusu sor.",
  "Ask something with what was found so far, or leave empty to go on with the research.":
    "Şimdiye kadar bulunanlarla bir şey sor, ya da araştırmaya devam etmek için boş bırak.",
  "continue the research": "araştırmaya devam et",
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
  "Thinking limit (characters)": "Düşünme sınırı (karakter)",
  "A reply that only thinks is stopped after this many characters and told to act. Raise it for hard problems; the token limit of a reply still applies.":
    "Yalnızca düşünen bir yanıt bu kadar karakterden sonra durdurulur ve harekete geçmesi istenir. Zor problemlerde yükselt; yanıtın token sınırı yine geçerlidir.",
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
  "Chat title (empty = automatic)": "Sohbet başlığı (boş = otomatik)", "Folder name": "Klasör adı",
  "code": "kod", "research": "araştırma", "chat": "sohbet", "queued": "sırada", "running": "çalışıyor", "done": "bitti", "stopped": "durdu",
  "failed": "başarısız", "cancelled": "iptal edildi", "interrupted": "yarıda kaldı", "open": "açık",
  "continue the plan": "plana devam et",
  // timeline
  "Tool calls": "Araç çağrıları", "Thinking": "Düşünme", "Model output": "Model çıktısı", "more lines": "satır daha",
  "Recalled from memory": "Bellekten hatırlananlar", "Gaps found — researching more": "Eksikler bulundu — araştırma sürüyor",
  "Stuck in long thinking {n} times: starting again with a clean context{notes}.": "Uzun düşünmede {n} kez takıldı: temiz bir bağlamla yeniden başlıyor{notes}.",
  "Sources": "Kaynak",
  "A quick answer names its sources and is checked with the tools; off: it may use your library but need not cite it": "Kısa yanıt kaynaklarını gösterir ve araçlarla kontrol edilir; kapalıyken kütüphaneni kullanabilir ama kaynak göstermek zorunda değildir",
  "Judge: the change solves the task.": "Hakem: değişiklik görevi çözüyor.", "Judge: not solved yet": "Hakem: henüz çözülmedi",
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
  "History": "Geçmiş", "Verifications": "Doğrulamalar", "unchanged lines": "değişmeyen satır",
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
  // the frame: sidebar, top bar, panel
  "Sections": "Bölümler", "Panel": "Panel", "Servers": "Sunucular", "servers": "sunucu",
  "Hide the sidebar (Ctrl+B)": "Kenar çubuğunu gizle (Ctrl+B)", "Hide the sidebar": "Kenar çubuğunu gizle",
  "Show the sidebar (Ctrl+B)": "Kenar çubuğunu göster (Ctrl+B)", "Show the sidebar": "Kenar çubuğunu göster",
  "Show or hide the panel (Ctrl+.)": "Paneli göster ya da gizle (Ctrl+.)", "Show or hide the panel": "Paneli göster ya da gizle",
  "Show or hide the sidebar": "Kenar çubuğunu göster ya da gizle", "Go to Offline": "Çevrimdışı'na git",
  "Environment": "Ortam", "Mode": "Mod", "Options": "Seçenekler", "Settings": "Ayarlar", "Context": "Bağlam",
  "Speed": "Hız", "Used": "Kullanılan", "required": "zorunlu", "not required": "zorunlu değil",
  "Images, notebook, options and tools": "Görseller, defter, seçenekler ve araçlar",
  "Model and settings of this chat (Ctrl+,)": "Bu sohbetin modeli ve ayarları (Ctrl+,)",
  "No sources": "Kaynaksız", "Document": "Belge", "Document (DOCX/PDF)": "Belge (DOCX/PDF)", "Document: ": "Belge: ",
  "Chapter written: {title}": "Bölüm yazıldı: {title}",
  "A chapter for the findings of each step, then the synthesis and the sources: a Word and PDF document":
    "Her adımın bulguları ayrı bir bölüm, sonda sentez ve kaynakça: Word ve PDF belge",
  "Long report": "Uzun rapor", "Reviewer": "Hakem", "No reviewer": "Hakemsiz",
  "An outline, then each section written and checked on its own: a much longer report, but slower":
    "Önce bir taslak, sonra her bölüm ayrı yazılır ve kontrol edilir: çok daha uzun bir rapor, ama daha yavaş",
  "A reviewer reads the report against the findings and sends real problems back to be fixed":
    "Hakem raporu bulgularla karşılaştırır ve gerçek sorunları düzeltilmek üzere geri gönderir",
  "A judge checks a finished task against the request and may send it back (normal speed only)":
    "Hakem biten işi istekle karşılaştırır ve gerekirse geri gönderir (yalnızca normal hızda)",
"{n} tool calls": "{n} araç çağrısı", "Copy": "Kopyala", "Copied": "Kopyalandı",
  "All projects": "Tüm projeler",
  "Every research keeps its plan, findings, sources and report in a project.": "Her araştırma planını, bulgularını, kaynaklarını ve raporunu bir projede saklar.",
  "Facts, lessons and preferences the agents learned; they are recalled when they fit a question.": "Ajanların öğrendiği bilgiler, dersler ve tercihler; bir soruya uyduklarında hatırlanırlar.",
  // the page of a new chat
  "Ctrl+Enter sends · Ctrl+K commands · Alt+N new chat": "Ctrl+Enter gönderir · Ctrl+K komutlar · Alt+N yeni sohbet",
  "Chat answers at once, with the tools you switch on in the + menu.": "Sohbet hemen cevaplar; + menüsünde açtığın araçları kullanır.",
  "Research answers a question from your library, Wikipedia and arXiv, with sources.": "Araştırma bir soruyu kütüphanenden, Wikipedia'dan ve arXiv'den kaynak göstererek cevaplar.",
  "Code writes, runs and tests a project in a folder of yours.": "Kod, senin bir klasöründe bir projeyi yazar, çalıştırır ve test eder.",
  "Explain the difference between TCP and UDP in simple terms.": "TCP ile UDP arasındaki farkı basitçe açıkla.",
  "Write a polite email that moves a meeting to Friday.": "Bir toplantıyı cumaya alan kibar bir e-posta yaz.",
  "What is a Fourier transform used for?": "Fourier dönüşümü ne işe yarar?",
  "Derive the ground state energy of the hydrogen atom.": "Hidrojen atomunun taban durum enerjisini türet.",
  "What limits the efficiency of silicon solar cells?": "Silisyum güneş hücrelerinin verimini ne sınırlar?",
  "How does CRISPR-Cas9 cut DNA?": "CRISPR-Cas9 DNA'yı nasıl keser?",
  "Add unit tests for the main module and make them pass.": "Ana modül için birim testleri ekle ve geçmelerini sağla.",
  "Find and fix the bug that makes the tests fail.": "Testlerin başarısız olmasına yol açan hatayı bul ve düzelt.",
  "Write a command-line tool that renames photos by the date they were taken.": "Fotoğrafları çekildikleri tarihe göre yeniden adlandıran bir komut satırı aracı yaz.",
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
