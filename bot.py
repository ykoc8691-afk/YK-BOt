import os
import re
import asyncio
import json
import time
import discord
from discord import app_commands
import sqlite3
import aiohttp
from datetime import timedelta
from discord.ext import tasks

TOKEN = os.getenv("DISCORD_TOKEN")  # Token'ı koda yazma, ortam değişkeni olarak ver
HOSGELDIN_KANAL_ADI = "genel"       # Hoş geldin mesajının atılacağı kanal
SOHBET_KANAL_ID = 1545356716252856377  # Transfer bilgisinin kişiyi etiketleyerek atılacağı kanal
HOSGELDIN_METNI = (
    "Kampımıza hoşgeldiniz, katılmak için transfer kanalındaki tike tıklayıp ardından aşağıda açılan "
    "transfer talep kanalından ticket açıp katılabilirsiniz!"
)
BAN_DOSYASI = "banlar.json"         # Ortak ban listesi (silme!)
MESAJ_DB = "mesajlar.db"            # Mesaj kayıtları (silinmiş mesaj listesi için)
MESAJ_SAKLAMA_GUN = 30              # Kayıtlar bu kadar gün sonra otomatik silinir
AYAR_DOSYASI = "ayarlar.json"       # /botcezakanal ve /cezakanal kanal ayarları
MUTE_DOSYASI = "mutelar.json"       # Aktif mute kayıtları (silme!)
OWNER_KULLANICI_ID = 1221139908471558144  # Sadece bu kullanıcı botu DM'den kontrol edebilir

# ---------- Yapay zeka (Gemini, ücretsiz) ----------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")  # Railway'de Variables'a eklenecek
GEMINI_MODEL = "gemini-3.5-flash-lite"        # En hızlı/hafif Flash-Lite modeli (daha yüksek ücretsiz kota hedefi)
AI_GECMIS_MESAJ = 6                           # Cevap verirken kaç önceki mesajı bağlam olarak okusun (az = daha az kota harcar)
AI_BEKLEME_SANIYE = 20                        # Aynı kişi art arda soruyu bu kadar saniyede bir sorabilir (kota koruma)
ai_son_kullanim = {}                          # kullanıcı_id -> son AI kullanım zamanı
AI_SISTEM_PROMPTU = (
    "Sen bir Discord kampı sunucusunun botusun. Samimi, esprili, hafif makaracı ve laf atan ama sevimli bir "
    "arkadaş gibisin; şakalaşır, takılırsın ama kırıcı olmazsın. ÖNEMLİ: Önce sana söyleneni yap veya soruyu net "
    "cevapla, espriyi ikinci planda tut. Cevaplar kısa, doğal ve Türkçe olsun. Bilmediğin bir şeyi uydurma, "
    "bilmiyorum de. KÜFÜR ETMEZSİN, hakaret/ırkçılık/cinsiyetçilik yapmazsın, kimseyi tehdit etmezsin ve yasa dışı "
    "hiçbir şeye yardım etmezsin."
)
OGRENILEN_DOSYASI = "ogrenilenler.json"       # Bota öğretilen bilgiler (sunucu_id -> [bilgi, ...])
MAKS_OGRENILEN = 100


def ogrenilenleri_getir(guild_id: int) -> list:
    return json_yukle(OGRENILEN_DOSYASI).get(str(guild_id), [])


def ogrenilen_ekle(guild_id: int, bilgi: str) -> int:
    veri = json_yukle(OGRENILEN_DOSYASI)
    liste = veri.get(str(guild_id), [])
    liste.append(bilgi[:500])
    veri[str(guild_id)] = liste[-MAKS_OGRENILEN:]
    json_kaydet(OGRENILEN_DOSYASI, veri)
    return len(veri[str(guild_id)])


AI_SERT_PROMPTU = (
    "Sen bir Discord kampı sunucusunun botusun. Sert, agresif, sivri dilli ve sabırsız bir üslubun var; "
    "yaltaklanmaz, uzun uzun özür dilemezsin, laf sokarsın. Ama KÜFÜR ETMEZSİN, hakaret/ırkçılık/cinsiyetçilik "
    "yapmazsın, kimseyi gerçekten tehdit etmezsin ve yasa dışı hiçbir şeye yardım etmezsin — bunlar tartışmaya "
    "kapalı sınırlar. Cevapların kısa ve Türkçe olsun, gereksiz uzatma."
)


def ai_mod_getir(guild_id: int) -> str:
    return json_yukle(AYAR_DOSYASI).get(str(guild_id), {}).get("ai_mod", "normal")


def ai_sistem_prompt(guild_id: int) -> str:
    temel = AI_SERT_PROMPTU if ai_mod_getir(guild_id) == "sert" else AI_SISTEM_PROMPTU
    bilgiler = ogrenilenleri_getir(guild_id)[-40:]
    if not bilgiler:
        return temel
    metin = "\n".join(f"- {b}" for b in bilgiler)[:4000]
    return (temel + "\n\nSana öğretilen bilgiler (doğru kabul et, ilgili soru gelirse bunları kullan):\n" + metin)


def gecmis_ara(guild_id: int, soru: str, limit: int = 6) -> list:
    """Sunucuda geçmişte yazılmış (mesaj kaydından) soruyla ilgili mesajları bulur."""
    kelimeler = [k for k in re.findall(r"\w+", soru.lower()) if len(k) >= 4][:4]
    if not kelimeler:
        return []
    kosul = " OR ".join("lower(icerik) LIKE ?" for _ in kelimeler)
    satirlar = db.execute(
        f"SELECT yazar_ad, icerik FROM mesajlar WHERE sunucu_id=? AND icerik != '' AND icerik NOT LIKE '<@%' "
        f"AND ({kosul}) ORDER BY yazilma DESC LIMIT ?",
        (guild_id, *[f"%{k}%" for k in kelimeler], limit),
    ).fetchall()
    return [f"{ad}: {ic[:200]}" for ad, ic in reversed(satirlar)]


GROQ_API_KEY = os.getenv("GROQ_API_KEY")      # Yedek ücretsiz yapay zeka (Gemini çalışmazsa devreye girer)
GROQ_MODEL = "llama-3.3-70b-versatile"


async def _gemini_dene(sistem: str, kullanici: str, json_mod: bool, token: int):
    """Başarılıysa metin, başarısızsa None döner."""
    if not GEMINI_API_KEY:
        return None
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent?key={GEMINI_API_KEY}"
    ayar = {"maxOutputTokens": token}
    if json_mod:
        ayar["responseMimeType"] = "application/json"
    gövde = {
        "system_instruction": {"parts": [{"text": sistem}]},
        "contents": [{"role": "user", "parts": [{"text": kullanici}]}],
        "generationConfig": ayar,
    }
    try:
        async with aiohttp.ClientSession() as oturum:
            async with oturum.post(url, json=gövde, timeout=aiohttp.ClientTimeout(total=25)) as yanit:
                if yanit.status != 200:
                    return None
                veri = await yanit.json()
                adaylar = veri.get("candidates", [])
                if not adaylar:
                    return None
                parcalar = adaylar[0].get("content", {}).get("parts", [])
                metin = "".join(p.get("text", "") for p in parcalar).strip()
                return metin or None
    except Exception:
        return None


async def _groq_dene(sistem: str, kullanici: str, json_mod: bool, token: int):
    if not GROQ_API_KEY:
        return None
    gövde = {
        "model": GROQ_MODEL,
        "messages": [
            {"role": "system", "content": sistem},
            {"role": "user", "content": kullanici},
        ],
        "max_tokens": token,
    }
    if json_mod:
        gövde["response_format"] = {"type": "json_object"}
    try:
        async with aiohttp.ClientSession() as oturum:
            async with oturum.post(
                "https://api.groq.com/openai/v1/chat/completions", json=gövde,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                timeout=aiohttp.ClientTimeout(total=25),
            ) as yanit:
                if yanit.status != 200:
                    return None
                veri = await yanit.json()
                metin = veri["choices"][0]["message"]["content"].strip()
                return metin or None
    except Exception:
        return None


async def ai_calistir(sistem: str, kullanici: str, json_mod: bool = False, token: int = 300):
    """Önce Gemini, olmazsa Groq. İkisi de olmazsa None."""
    for deneme in (_gemini_dene, _groq_dene):
        metin = await deneme(sistem, kullanici, json_mod, token)
        if metin:
            return metin
    return None


async def gemini_cevap(soru: str, baglam: str, sistem: str = None) -> str:
    if not GEMINI_API_KEY and not GROQ_API_KEY:
        return "Yapay zeka şu an kapalı (API anahtarı tanımlı değil)."
    metin = await ai_calistir(sistem or AI_SISTEM_PROMPTU, f"{baglam}\n\nSoru: {soru}")
    if metin:
        return metin[:1900]
    return "Şu an iki yapay zeka da cevap vermiyor (limit dolmuş ya da teknik sorun var), biraz sonra tekrar dene."


async def sohbet_baglami(message: discord.Message) -> str:
    """Kanaldaki son birkaç mesajı (botun cevapları dahil) kısa bir bağlam metni yapar. Yapay zeka API'si harcamaz."""
    satirlar = []
    async for onceki in message.channel.history(limit=AI_GECMIS_MESAJ, before=message):
        if onceki.content:
            satirlar.append(f"{onceki.author.display_name}: {onceki.content[:300]}")
    satirlar.reverse()
    return "Son konuşmalar:\n" + "\n".join(satirlar) if satirlar else ""


YONETIM_KELIMELERI = ("kanal", "kategori", "rol", "izin", "yetki", "görs", "göreb", "görün", "ayarla",
                      "oluştur", "ekle", "adını", "ismini", "sesli", "yazılı", "yazamas", "yazabil")


def yonetim_gibi_mi(soru: str) -> bool:
    """Ucuz ön eleme: yönetim kelimesi yoksa yönetim isteği için yapay zekaya hiç sorma (API tasarrufu)."""
    k = soru.lower()
    return any(w in k for w in YONETIM_KELIMELERI)


async def ogrenme_logla(guild: discord.Guild, metin: str):
    kid = json_yukle(AYAR_DOSYASI).get(str(guild.id), {}).get("ogrenme_log_kanal")
    kanal = guild.get_channel(kid) if kid else None
    if kanal:
        try:
            await kanal.send(metin[:1900])
        except discord.HTTPException:
            pass


# ---------- Yapay zekayla sunucu yönetimi (/yap veya "@bot yap: ...") ----------
YAP_SISTEM = (
    "Sen bir Discord sunucu yönetim asistanısın. Kullanıcının isteğini aşağıdaki işlemlere çevir ve SADECE JSON döndür: "
    '{"yonetim_istegi":true|false,"islemler":[...],"not":"yapılamayan şeyler için kısa açıklama (yoksa boş)"}.\n'
    "İşlemler (SIRAYLA uygulanır; önce oluşturulması gereken şeyler önce yazılmalı):\n"
    '- {"islem":"kategori_olustur","ad":"...","ref":"k1"}\n'
    '- {"islem":"kanal_olustur","ad":"...","tur":"yazi"|"ses","kategori":<kategori id | önceki işlemin ref\'i | null>,"ref":"c1"}\n'
    '- {"islem":"kategori_sirala","kategori":<id | ref>,"konum":"en_ust"|"en_alt"}\n'
    '- {"islem":"kategori_gorunurluk","kategori":<id | ref>,"rol":<rol id>,"mod":"ve_ustu"|"sadece"}  '
    '(kategorinin ve içindeki TÜM kanalların sadece o rolü ("sadece") veya o rol ve hiyerarşide üstündeki rolleri ("ve_ustu") '
    "görmesini sağlar; @everyone görmez)\n"
    '- {"islem":"kanal_izin","kanal":<kanal id | ref>,"rol":<rol id>,"kapsam":"rol"|"ve_ustu"|"hepsi","ver":[...],"engelle":[...]}  '
    "(kapsam: sadece o rol / o rol ve hiyerarşide üstündekiler / sunucudaki tüm roller (hepsi, rol yazma). "
    "ver ve engelle listelerinde kullanılabilecek izinler: view_channel, send_messages, read_message_history, manage_messages, "
    "add_reactions, attach_files, embed_links, mention_everyone, create_public_threads, send_messages_in_threads, use_external_emojis. "
    "'Mesaj silme yetkisi' = manage_messages. 'Yönetici hariç kimse X yapamasın' = kapsam hepsi + engelle (yöneticiler zaten etkilenmez))\n"
    '- {"islem":"kanal_adi","kanal":<id | ref>,"yeni_ad":"..."}\n'
    '- {"islem":"rol_olustur","ad":"...","renk":"#RRGGBB"}  (renk opsiyonel)\n'
    '- {"islem":"rol_ver","kisi":<kullanıcı id>,"rol":<rol id>}\n'
    '- {"islem":"rol_al","kisi":<kullanıcı id>,"rol":<rol id>}\n'
    '- {"islem":"izin_sorgu","kanal":<kanal id>,"rol":<rol id veya null>,"kisi":<kullanıcı id veya null>,"detay":true|false}  '
    '(sadece soru: "X şu kanalı görebiliyor mu / yetkilerini say" gibi; hiçbir şeyi değiştirmez. '
    "Yetkileri listele/say deniyorsa detay true, evet-hayır sorusuysa false)\n"
    "Henüz var olmayan bir kategori/kanal için 'ref' ver ve sonraki işlemlerde id yerine o ref'i yaz. "
    "En fazla 12 işlem. Rol ve kanal için verilen listedeki id'leri kullan, kullanıcılar istekte <@id> olarak geçer. "
    "'Herkes' demek @everyone rolüdür. Listede olmayan bir şey istenirse işlem ekleme, not'a yaz. "
    "Silme işlemleri ve yönetici izni verme DESTEKLENMİYOR; istenirse not'a yaz. "
    "yonetim_istegi: mesaj sunucuda rol/kanal/izin işi veya izin sorusuysa true, "
    "sohbet/genel soru/espri ise false (o zaman islemler boş, not boş olsun). "
    "'Son konuşmalar' sadece bağlam içindir; 'sesli olmasın' gibi kısa devam mesajları önceki isteğe aittir, ona göre yorumla. "
    "\"Bu kanal\" denirse 'İsteğin yazıldığı kanal' olarak verilen kanaldır."
)

IZIN_ADLARI = {
    "view_channel": "kanalı görme", "send_messages": "mesaj yazma", "read_message_history": "geçmişi okuma",
    "manage_messages": "mesaj silme/yönetme", "add_reactions": "tepki ekleme", "attach_files": "dosya ekleme",
    "embed_links": "link önizleme", "mention_everyone": "@everyone etiketleme",
    "create_public_threads": "konu açma", "send_messages_in_threads": "konularda yazma",
    "use_external_emojis": "dış emoji kullanma",
}


async def yap_hazirla(guild: discord.Guild, istek: str, kanal=None, baglam: str = ""):
    """İsteği işlem listesine çevirir. (islemler, not) döner; yapay zeka cevap vermezse (None, hata)."""
    roller = "; ".join(f"{r.name}={r.id}" for r in guild.roles[:100])
    kanallar = "; ".join(f"{k.name} [{k.type.name}]={k.id}" for k in guild.channels[:150])
    suan = f"\nİsteğin yazıldığı kanal: {kanal.name}={kanal.id}" if kanal else ""
    metin = await ai_calistir(YAP_SISTEM, f"Roller: {roller}\nKanallar: {kanallar}{suan}\n\n{baglam}\n\nİstek: {istek}",
                              json_mod=True, token=2000)
    if not metin:
        return None, "Yapay zeka şu an cevap vermiyor, biraz sonra tekrar dene."
    try:
        metin = re.sub(r"^```(?:json)?|```$", "", metin.strip(), flags=re.M).strip()
        veri = json.loads(metin)
        islemler = [i for i in veri.get("islemler", []) if isinstance(i, dict)][:12]
        not_ = str(veri.get("not", "") or "")
        if not islemler and not veri.get("yonetim_istegi"):
            not_ = ""  # sohbet mesajı: yönetim notu verme, normal sohbete düşsün
        return islemler, not_
    except (json.JSONDecodeError, AttributeError):
        return None, "İsteği anlayamadım, daha net yazar mısın?"


def _id_ad(guild: discord.Guild, tur: str, deger, refs: dict = None):
    """id (veya önceki işlemde oluşturulan 'ref') -> kanal/rol/üye nesnesi."""
    if refs and isinstance(deger, str) and deger in refs:
        return refs[deger]
    try:
        i = int(deger)
    except (TypeError, ValueError):
        return None
    return {"kanal": guild.get_channel, "rol": guild.get_role, "kisi": guild.get_member}[tur](i)


def _gorunur_roller(guild: discord.Guild, rol: discord.Role, mod) -> list:
    """'ve_ustu': hiyerarşide bu rol ve üstündeki roller; 'sadece': yalnızca bu rol."""
    if mod == "sadece":
        return [rol]
    return [r for r in guild.roles if r.position >= rol.position and r.id != guild.id]


def _kapsam_rolleri(guild: discord.Guild, rol, kapsam) -> list:
    if kapsam == "hepsi":
        return list(guild.roles)
    if not rol:
        return []
    return _gorunur_roller(guild, rol, "ve_ustu") if kapsam == "ve_ustu" else [rol]


def _izin_listesi(liste) -> list:
    return [p for p in (liste or []) if isinstance(p, str) and p in IZIN_ADLARI]


def yap_tanimlar(guild: discord.Guild, islemler: list) -> list:
    """Her işlemi okunur bir cümleye çevirir; henüz oluşturulmamış 'ref'ler plandaki adlarıyla gösterilir."""
    ref_adlari = {i["ref"]: i.get("ad") for i in islemler if isinstance(i.get("ref"), str) and i.get("ad")}
    return [yap_tanim(guild, i, ref_adlari) for i in islemler]


def yap_tanim(guild: discord.Guild, i: dict, ref_adlari: dict = None) -> str:
    ref_adlari = ref_adlari or {}

    def kanal_adi(deger):
        if isinstance(deger, str) and deger in ref_adlari:
            return f"{ref_adlari[deger]} (yeni)"
        k = _id_ad(guild, "kanal", deger)
        return k.name if k else "?"

    t = i.get("islem")
    rol = _id_ad(guild, "rol", i.get("rol"))
    kisi = _id_ad(guild, "kisi", i.get("kisi"))
    if t == "kategori_olustur":
        return f"**{i.get('ad')}** adında kategori oluştur"
    if t == "kanal_olustur":
        kat = f" ({kanal_adi(i.get('kategori'))} kategorisinde)" if i.get("kategori") else ""
        return f"**{i.get('ad')}** adında {'ses' if i.get('tur') == 'ses' else 'yazı'} kanalı oluştur{kat}"
    if t == "kategori_sirala":
        return f"**{kanal_adi(i.get('kategori'))}** kategorisini {'en üste' if i.get('konum') != 'en_alt' else 'en alta'} taşı"
    if t == "kategori_gorunurluk":
        if not rol:
            return "Kategori görünürlüğü ayarla (rol bulunamadı)"
        kapsam = f"**{rol.name}** ve üstü ({len(_gorunur_roller(guild, rol, 've_ustu'))} rol)" \
            if i.get("mod") != "sadece" else f"sadece **{rol.name}**"
        return f"**{kanal_adi(i.get('kategori'))}** kategorisi ve içindeki tüm kanallar: {kapsam} görsün, @everyone ve diğerleri görmesin"
    if t == "kanal_izin":
        kapsam = i.get("kapsam") or "rol"
        if kapsam == "hepsi":
            kim = "**tüm roller**"
        elif kapsam == "ve_ustu":
            kim = f"**{rol.name if rol else '?'}** ve üstü roller"
        else:
            kim = f"**{rol.name if rol else '?'}** rolü"
        ver = [IZIN_ADLARI[p] for p in _izin_listesi(i.get("ver"))]
        engel = [IZIN_ADLARI[p] for p in _izin_listesi(i.get("engelle"))]
        eski = {"gorsun": "görsün", "gormesin": "görmesin", "yazsin": "yazabilsin", "yazmasin": "yazamasın", "sifirla": "izni sıfırlansın"}
        parca = []
        if ver:
            parca.append("verilen: " + ", ".join(ver))
        if engel:
            parca.append("engellenen: " + ", ".join(engel))
        if i.get("izin") in eski:
            parca.append(eski[i["izin"]])
        return f"**#{kanal_adi(i.get('kanal'))}** kanalında {kim} → " + ("; ".join(parca) or "?")
    if t == "rol_olustur":
        return f"**{i.get('ad')}** adında rol oluştur"
    if t in ("rol_ver", "rol_al"):
        return f"**{kisi.display_name if kisi else '?'}** kişisine **{rol.name if rol else '?'}** rolünü {'ver' if t == 'rol_ver' else 'al'}"
    if t == "kanal_adi":
        return f"**#{kanal_adi(i.get('kanal'))}** kanalının adını **{i.get('yeni_ad')}** yap"
    return f"Desteklenmeyen işlem: {t}"


async def yap_uygula(guild: discord.Guild, i: dict, yapan: str, refs: dict, ref_adlari: dict = None) -> str:
    t = i.get("islem")
    sebep = f"Bot yönetimi: {yapan}"
    tanim = yap_tanim(guild, i, ref_adlari)
    rol = _id_ad(guild, "rol", i.get("rol"))
    kisi = _id_ad(guild, "kisi", i.get("kisi"))
    kanal = _id_ad(guild, "kanal", i.get("kanal") or i.get("kategori"), refs)
    try:
        if t == "kategori_olustur":
            nesne = await guild.create_category(str(i.get("ad"))[:100], reason=sebep)
            if isinstance(i.get("ref"), str):
                refs[i["ref"]] = nesne
        elif t == "kanal_olustur":
            kategori = _id_ad(guild, "kanal", i.get("kategori"), refs)
            kategori = kategori if isinstance(kategori, discord.CategoryChannel) else None
            ad = str(i.get("ad"))[:100]
            if i.get("tur") == "ses":
                nesne = await guild.create_voice_channel(ad, category=kategori, reason=sebep)
            else:
                nesne = await guild.create_text_channel(ad, category=kategori, reason=sebep)
            if isinstance(i.get("ref"), str):
                refs[i["ref"]] = nesne
        elif t == "kategori_sirala":
            if not isinstance(kanal, discord.CategoryChannel):
                return f"❌ {tanim} (kategori bulunamadı)"
            konum = 0 if i.get("konum") != "en_alt" else len(guild.categories)
            await kanal.edit(position=konum, reason=sebep)
        elif t == "kategori_gorunurluk":
            if not isinstance(kanal, discord.CategoryChannel) or not rol:
                return f"❌ {tanim} (kategori veya rol bulunamadı)"
            izinli = _gorunur_roller(guild, rol, i.get("mod"))
            izinli_idler = {r.id for r in izinli}
            hedefler = {kanal.id: kanal}
            for c in list(kanal.channels) + [v for v in refs.values() if getattr(v, "category_id", None) == kanal.id]:
                hedefler[c.id] = c
            for h in hedefler.values():
                await h.set_permissions(guild.default_role, view_channel=False, reason=sebep)
                for r in izinli:
                    await h.set_permissions(r, view_channel=True, reason=sebep)
                # İzinli olmayan rollerde eskiden verilmiş "görsün" izni varsa kaldır
                for hedef_, ow in list(h.overwrites.items()):
                    if isinstance(hedef_, discord.Role) and hedef_.id != guild.id and hedef_.id not in izinli_idler \
                            and ow.view_channel is True:
                        ow.view_channel = None
                        await h.set_permissions(hedef_, overwrite=(None if ow.is_empty() else ow), reason=sebep)
                await h.set_permissions(guild.me, view_channel=True, reason=sebep)  # Bot kendini kilitlemesin
        elif t == "kanal_izin":
            if not kanal:
                return f"❌ {tanim} (kanal bulunamadı)"
            kapsam = i.get("kapsam") or "rol"
            roller = _kapsam_rolleri(guild, rol, kapsam)
            if not roller:
                return f"❌ {tanim} (rol bulunamadı)"
            ver, engel = _izin_listesi(i.get("ver")), _izin_listesi(i.get("engelle"))
            eski = {"gorsun": ("view_channel", True), "gormesin": ("view_channel", False),
                    "yazsin": ("send_messages", True), "yazmasin": ("send_messages", False)}
            sifirla = i.get("izin") == "sifirla"
            if not (ver or engel or sifirla or i.get("izin") in eski):
                return f"❌ {tanim} (yapılacak izin belirtilmemiş)"
            for r in roller:
                if sifirla:
                    await kanal.set_permissions(r, overwrite=None, reason=sebep)
                    continue
                ow = kanal.overwrites_for(r)
                for p in ver:
                    setattr(ow, p, True)
                for p in engel:
                    setattr(ow, p, False)
                if i.get("izin") in eski:
                    setattr(ow, eski[i["izin"]][0], eski[i["izin"]][1])
                await kanal.set_permissions(r, overwrite=ow, reason=sebep)
        elif t == "rol_olustur":
            renk = discord.Color.default()
            m = re.fullmatch(r"#?([0-9a-fA-F]{6})", str(i.get("renk") or ""))
            if m:
                renk = discord.Color(int(m.group(1), 16))
            await guild.create_role(name=str(i.get("ad"))[:100], color=renk, reason=sebep)
        elif t in ("rol_ver", "rol_al"):
            if not kisi or not rol:
                return f"❌ {tanim} (kişi veya rol bulunamadı)"
            if rol.permissions.administrator:
                return f"❌ {tanim} (yönetici yetkili roller bot üzerinden verilip alınmaz)"
            if t == "rol_ver":
                await kisi.add_roles(rol, reason=sebep)
            else:
                await kisi.remove_roles(rol, reason=sebep)
        elif t == "kanal_adi":
            if not kanal:
                return f"❌ {tanim} (kanal bulunamadı)"
            await kanal.edit(name=str(i.get("yeni_ad"))[:100], reason=sebep)
        else:
            return f"❌ {tanim}"
    except discord.Forbidden:
        return f"❌ {tanim} (botun yetkisi yetmedi; bot rolü hedef rolün/kanalın üstünde olmalı)"
    except discord.HTTPException as e:
        return f"❌ {tanim} (Discord hatası: {str(e.text)[:80]})"
    except Exception as e:  # beklenmeyen hata: konsola yaz, kullanıcıya kısa bilgi ver
        import traceback
        traceback.print_exc()
        return f"❌ {tanim} (beklenmeyen hata: {type(e).__name__})"
    return f"✅ {tanim}"


def izin_sorgu_cevabi(guild: discord.Guild, i: dict) -> str:
    kanal = _id_ad(guild, "kanal", i.get("kanal"))
    rol = _id_ad(guild, "rol", i.get("rol"))
    kisi = _id_ad(guild, "kisi", i.get("kisi"))
    if not kanal or not (rol or kisi):
        return "❓ Kanal veya rol/kişi bulunamadı."
    hedef = kisi if kisi else rol
    p = kanal.permissions_for(hedef)
    ad = kisi.display_name if kisi else rol.name
    if p.administrator:
        return f"**{ad}** yönetici yetkisine sahip, **#{kanal.name}** dahil her kanalı görür ve yazabilir."
    if i.get("detay"):
        liste = [
            ("view_channel", "Kanalı görme"), ("send_messages", "Mesaj gönderme"),
            ("read_message_history", "Mesaj geçmişini okuma"), ("attach_files", "Dosya ekleme"),
            ("embed_links", "Link önizleme"), ("add_reactions", "Tepki ekleme"),
            ("use_external_emojis", "Dış emoji kullanma"), ("create_public_threads", "Konu açma"),
            ("send_messages_in_threads", "Konularda yazma"), ("mention_everyone", "@everyone/@here etiketleme"),
            ("manage_messages", "Mesajları yönetme"), ("manage_channels", "Kanalı yönetme"),
            ("manage_permissions", "Kanal izinlerini yönetme"),
        ]
        if isinstance(kanal, discord.VoiceChannel):
            liste = [("view_channel", "Kanalı görme"), ("connect", "Bağlanma"), ("speak", "Konuşma"),
                     ("stream", "Yayın açma"), ("mute_members", "Üyeleri susturma")]
        var = [ad_ for k, ad_ in liste if getattr(p, k, False)]
        yok = [ad_ for k, ad_ in liste if not getattr(p, k, False)]
        return (f"**{ad}** → **#{kanal.name}** yetkileri:\n✅ " + (", ".join(var) or "yok") +
                "\n❌ " + (", ".join(yok) or "yok"))
    gor = "görebilir ✅" if p.view_channel else "göremez ❌"
    if isinstance(kanal, discord.VoiceChannel):
        yaz = "bağlanabilir ✅" if p.connect else "bağlanamaz ❌"
    else:
        yaz = "yazabilir ✅" if p.send_messages else "yazamaz ❌"
    return f"**{ad}** → **#{kanal.name}**: {gor}, {yaz}"


def sorgu_ayir(guild: discord.Guild, islemler: list):
    """(sorgu cevapları, değişiklik işlemleri) döner. Sorgular onay istemeden hemen cevaplanır."""
    sorgular = [izin_sorgu_cevabi(guild, i) for i in islemler if i.get("islem") == "izin_sorgu"]
    degisiklikler = [i for i in islemler if i.get("islem") != "izin_sorgu"]
    return sorgular, degisiklikler


class YapOnay(discord.ui.View):
    def __init__(self, guild: discord.Guild, islemler: list, yetkililer: set):
        super().__init__(timeout=180)
        self.guild, self.islemler, self.yetkililer = guild, islemler, yetkililer

    @discord.ui.button(label="Onayla", style=discord.ButtonStyle.success)
    async def onayla(self, inter: discord.Interaction, button: discord.ui.Button):
        if inter.user.id not in self.yetkililer:
            return await inter.response.send_message("Bu buton sana ait değil.", ephemeral=True)
        self.stop()
        await inter.response.edit_message(content="Uygulanıyor...", view=None)
        refs, ref_adlari, sonuc = {}, {i["ref"]: i.get("ad") for i in self.islemler
                                       if isinstance(i.get("ref"), str) and i.get("ad")}, []
        for i in self.islemler:
            sonuc.append(await yap_uygula(self.guild, i, str(inter.user), refs, ref_adlari))
        await inter.edit_original_response(content="\n".join(sonuc)[:1900])

    @discord.ui.button(label="İptal", style=discord.ButtonStyle.secondary)
    async def iptal(self, inter: discord.Interaction, button: discord.ui.Button):
        if inter.user.id not in self.yetkililer:
            return await inter.response.send_message("Bu buton sana ait değil.", ephemeral=True)
        self.stop()
        await inter.response.edit_message(content="İptal edildi.", view=None)


def yap_yetkilisi_mi(kullanici_id: int, guild: discord.Guild) -> bool:
    return kullanici_id in (OWNER_KULLANICI_ID, guild.owner_id)


async def yap_metni_ve_gorunum(guild: discord.Guild, istek: str, kanal=None, baglam: str = ""):
    """(mesaj metni, view veya None) döner."""
    islemler, not_ = await yap_hazirla(guild, istek, kanal, baglam)
    if islemler is None:
        return not_, None
    sorgular, islemler = sorgu_ayir(guild, islemler)
    if not islemler and not sorgular:
        return "Yapılacak bir işlem çıkaramadım." + (f"\nNot: {not_}" if not_ else ""), None
    metin = "\n".join(sorgular)
    if not islemler:
        return (metin + (f"\nNot: {not_}" if not_ else ""))[:1900], None
    satirlar = "\n".join(f"{n}. {t_}" for n, t_ in enumerate(yap_tanimlar(guild, islemler), 1))
    metin += ("\n\n" if metin else "") + f"Şunları yapacağım:\n{satirlar}\n" + (f"\nNot: {not_}\n" if not_ else "") + "\nOnaylıyor musun?"
    return metin[:1900], YapOnay(guild, islemler, {OWNER_KULLANICI_ID, guild.owner_id})


# ---------- Otomod ayarları ----------
KUFUR_MUTE_SURESI = 7 * 86400   # Küfür: 7 gün mute
LINK_MUTE_SURESI = 30 * 86400   # Link: 30 gün mute
SPAM_MUTE_SURESI = 30 * 86400   # Spam: 30 gün mute
SPAM_SINIRI = 10                # Üst üste kaç aynı mesaj spam sayılsın
MAKS_TIMEOUT = 27 * 86400       # Discord zaman aşımı en fazla 28 gün; daha uzun mute'lar parça parça uzatılır

# OTOMOD-BASLANGIC
LINK_REGEX = re.compile(
    r"(https?://|www\.|discord\.gg/|discord(app)?\.com/invite/)"
    r"|\b[a-z0-9-]+\.(com|net|org|info|biz|xyz|gg|io|tk|app|link|site|online|shop|top|club|ru|ly)\b",
    re.IGNORECASE,
)

# Küfür listesi: yazım hileleri (ş/s, ı/i, a4, tekrar eden harf) normalize edilir.
# Kelimeleri buradan ekleyip çıkarabilirsin. Yazılışlar normalize (ASCII, tekrarsız harf) olmalı.
KUFUR_TAM = {"amk", "aq", "amq"}  # Tam eşleşme (kısaltmalar)
KUFUR_KOKLERI = (                 # Bu köklerle başlayan kelimeler
    "siktir", "sikeyim", "sikerim", "orospu", "yarak", "amcik", "gotun",
    "gotveren", "pezevenk", "ibne", "gavat", "kahpe",
)
KUFUR_IFADELER = ("amina koy", "amina kod", "anani sik", "ananin ami")

_HARITA = str.maketrans({
    "ı": "i", "ş": "s", "ç": "c", "ğ": "g", "ö": "o", "ü": "u",
    "0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s",
})


def kufur_var_mi(metin: str) -> bool:
    n = metin.replace("İ", "i").lower().translate(_HARITA)
    belirteler = [re.sub(r"(.)\1+", r"\1", t) for t in re.findall(r"[a-z]+", n)]
    for t in belirteler:
        if t in KUFUR_TAM or t.startswith(KUFUR_KOKLERI):
            return True
    birlesik = " ".join(belirteler)
    return any(i in birlesik for i in KUFUR_IFADELER)
# OTOMOD-BITIS

# (sunucu_id, kullanici_id) -> {"icerik": son mesaj, "sayi": üst üste kaç kez}
spam_durum = {}

# Mesaj kayıt veritabanı
db = sqlite3.connect(MESAJ_DB)
db.execute(
    """CREATE TABLE IF NOT EXISTS mesajlar (
        id INTEGER PRIMARY KEY, sunucu_id INTEGER, kanal_id INTEGER,
        yazar_id INTEGER, yazar_ad TEXT, icerik TEXT, ekler TEXT,
        yazilma INTEGER, silinme INTEGER)"""
)
db.commit()

# /clear ile silinen mesajların ID'leri (bunlar "silinmiş mesaj" listesine girmesin)
clear_idleri = set()

intents = discord.Intents.default()
intents.members = True          # Developer Portal > Bot > "Server Members Intent" açık olmalı
intents.message_content = True  # Developer Portal > Bot > "Message Content Intent" açık olmalı (!format için)

FORMAT_METNI = """İstenilen rütbe:
İstenilen branş:
İstenilen branş rütbe:
İstenilen birim:
İstenilen birim rütbe:
İstenilen tim:
İstenilen tim rütbe:
Ekstra istek:
Tecrübeli olunan kamplar ve bu kamplarda rütbeleriniz:
Tecrübe kanıt SS'leri:"""

# !linkler komutuyla atılacak linkler
LINKLER_METNI = (
    "YK Studio Ana Roblox Grubu: https://www.roblox.com/share/g/277876846\n"
    "Oyun linki: https://www.roblox.com/share?code=33ba1a0729622f4f8e8daa5a11cc427f"
    "&type=ExperienceDetails&stamp=1792313936448"
)

# Yeni açılan kanalın adında bu kelimelerden biri geçiyorsa bot otomatik !format atar (ticket kanalları)
TICKET_KANAL_ANAHTARLARI = ("ticket",)

# Bot sunucuya eklenirken davet linki bu izinleri ister
GEREKLI_IZINLER = discord.Permissions(
    view_channel=True,          # Mesajları görüp kaydedebilmesi için
    send_messages=True,
    embed_links=True,
    read_message_history=True,
    manage_messages=True,       # /clear için
    create_instant_invite=True, # /davet için
    ban_members=True,           # /ban, /databan, /unban ve otomod banı için
    moderate_members=True,      # /mute ve otomod susturması için
    mention_everyone=True,      # Otomod uyarılarında @here etiketi için
    manage_roles=True,          # /yap ile rol verme/oluşturma için
    manage_channels=True,       # /yap ile kanal izni/oluşturma için
)


# ---------- Ortak ban listesi (JSON) ----------
# Yapı: { "kullanici_id": {"bitis": unix_zaman | None, "sunucu_id": int, "sebep": str} }
# bitis = None  -> süresiz ban (/databan)
def banlari_yukle() -> dict:
    try:
        with open(BAN_DOSYASI, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def banlari_kaydet(veri: dict):
    with open(BAN_DOSYASI, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)


def ban_aktif_mi(kayit: dict) -> bool:
    return kayit["bitis"] is None or kayit["bitis"] > time.time()


SURE_BIRIMLERI = {"dk": 60, "m": 60, "sa": 3600, "h": 3600, "g": 86400, "d": 86400, "hf": 604800, "w": 604800}


def sure_coz(metin: str):
    """'30dk', '2sa', '7g', '1hf' gibi süreyi saniyeye çevirir. Geçersizse None."""
    m = re.fullmatch(r"(\d+)\s*(dk|m|sa|h|g|d|hf|w)", metin.strip().lower())
    if not m:
        return None
    saniye = int(m.group(1)) * SURE_BIRIMLERI[m.group(2)]
    return saniye if saniye > 0 else None


# ---------- Genel JSON yardımcıları (ayarlar, mute kayıtları) ----------
def json_yukle(dosya: str) -> dict:
    try:
        with open(dosya, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def json_kaydet(dosya: str, veri: dict):
    with open(dosya, "w", encoding="utf-8") as f:
        json.dump(veri, f, ensure_ascii=False, indent=2)


# ---------- Mute (Discord zaman aşımı) ----------
# Kayıt: "sunucu_id:kullanici_id" -> {"bitis": mute'un bitiş zamanı, "kesim": mevcut zaman aşımı parçasının bitişi}
async def mute_uygula(uye: discord.Member, saniye: int, sebep: str) -> int:
    """Üyeyi susturur, bitiş zamanını (unix) döner. 28 günden uzun süreler parça parça uzatılır."""
    simdi = int(time.time())
    parca = min(saniye, MAKS_TIMEOUT)
    await uye.timeout(timedelta(seconds=parca), reason=sebep[:500])
    kayitlar = json_yukle(MUTE_DOSYASI)
    kayitlar[f"{uye.guild.id}:{uye.id}"] = {"bitis": simdi + saniye, "kesim": simdi + parca}
    json_kaydet(MUTE_DOSYASI, kayitlar)
    return simdi + saniye


async def mute_kaldir(uye: discord.Member, sebep: str):
    kayitlar = json_yukle(MUTE_DOSYASI)
    kayitlar.pop(f"{uye.guild.id}:{uye.id}", None)
    json_kaydet(MUTE_DOSYASI, kayitlar)
    await uye.timeout(None, reason=sebep[:500])


# ---------- Ceza logları ----------
# /cezakanal      -> tüm mute ve banlar
# /botcezakanal   -> sadece otomod (küfür/link/spam) cezaları (bunlar /cezakanal'a da düşer)
async def ceza_logla(guild, tur, kisi, sebep, bitis=None, yetkili=None, kanal=None,
                     mesaj=None, otomod=False, here=False):
    ayar = json_yukle(AYAR_DOSYASI).get(str(guild.id), {})
    hedefler = {ayar.get("cezakanal")}
    if otomod:
        hedefler.add(ayar.get("botcezakanal"))
    hedefler.discard(None)
    if not hedefler:
        return

    embed = discord.Embed(title=tur, color=discord.Color.orange(), timestamp=discord.utils.utcnow())
    embed.add_field(name="Kişi", value=f"{kisi} (<@{kisi.id}>)", inline=False)
    if otomod:
        yetkili_metni = "Otomatik (bot)"
    else:
        yetkili_metni = f"<@{yetkili.id}>" if yetkili else "-"
    embed.add_field(name="Yetkili", value=yetkili_metni, inline=True)
    if bitis:
        embed.add_field(name="Bitiş", value=f"<t:{bitis}:F> (<t:{bitis}:R>)", inline=True)
    if kanal:
        embed.add_field(name="Kanal", value=f"<#{kanal.id}>", inline=True)
    embed.add_field(name="Sebep", value=sebep[:1000], inline=False)
    if mesaj:
        embed.add_field(name="Mesaj", value="```" + mesaj[:300].replace("`", "'") + "```", inline=False)

    for kid in hedefler:
        hedef = guild.get_channel(int(kid))
        if hedef:
            try:
                await hedef.send(
                    content="@here" if here else None,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass


# ---------- Otomod ----------
async def otomod_mute(message: discord.Message, uye: discord.Member, saniye: int, sebep: str, here=False):
    try:
        bitis = await mute_uygula(uye, saniye, f"Otomod: {sebep}")
        tur = "Otomatik Mute"
        await dm_dene(
            uye,
            f"**{message.guild.name}** sunucusunda otomatik olarak susturuldun.\n"
            f"Sebep: {sebep}\nBitiş: <t:{bitis}:F>",
        )
    except discord.HTTPException:
        bitis, tur = None, "Otomatik Mute BAŞARISIZ (botun izni/rol sırasını kontrol et)"
    await ceza_logla(message.guild, tur, uye, sebep, bitis=bitis, kanal=message.channel,
                     mesaj=message.content, otomod=True, here=here)


async def otomod(message: discord.Message) -> bool:
    """Kural ihlali varsa cezayı uygular ve True döner (mesaj işlenmiş olur)."""
    uye = message.author
    if not isinstance(uye, discord.Member) or not message.content:
        return False
    p = uye.guild_permissions
    if p.administrator or p.manage_messages or p.moderate_members:
        return False  # Yetkililere otomod işlemez

    guild = message.guild
    icerik = message.content
    link_var = LINK_REGEX.search(icerik) is not None

    # Üst üste aynı mesaj sayacı
    norm = " ".join(icerik.lower().split())
    anahtar = (guild.id, uye.id)
    s = spam_durum.get(anahtar)
    if s and s["icerik"] == norm:
        s["sayi"] += 1
    else:
        s = {"icerik": norm, "sayi": 1}
    spam_durum[anahtar] = s

    async def sil():
        try:
            await message.delete()
        except discord.HTTPException:
            pass

    # 1) Spam yaparken link kullanma -> sunucudan yasakla
    if link_var and s["sayi"] >= 2:
        await sil()
        spam_durum.pop(anahtar, None)
        sebep = "Spam yaparken link kullandı"
        try:
            await guild.ban(uye, reason=f"Otomod: {sebep}")
            tur = "Otomatik Ban"
        except discord.HTTPException:
            tur = "Otomatik Ban BAŞARISIZ (botun izni/rol sırasını kontrol et)"
        await ceza_logla(guild, tur, uye, sebep, kanal=message.channel, mesaj=icerik, otomod=True)
        return True

    # 2) Üst üste aynı mesajı SPAM_SINIRI kez atma -> mute + log + @here
    if s["sayi"] >= SPAM_SINIRI:
        await sil()
        spam_durum.pop(anahtar, None)
        await otomod_mute(message, uye, SPAM_MUTE_SURESI,
                          f"Üst üste {SPAM_SINIRI} kez aynı mesajı gönderdi (spam)", here=True)
        return True

    # 3) Link atma -> mute + kanalda @here uyarısı + log
    if link_var:
        await sil()
        await otomod_mute(message, uye, LINK_MUTE_SURESI, "Link gönderdi")
        try:
            await message.channel.send(
                f"@here {uye.mention} bu kanalda link göndermeye çalıştı ve **30 gün** susturuldu. "
                f"Link paylaşmak yasaktır!",
                allowed_mentions=discord.AllowedMentions(everyone=True, users=True),
            )
        except discord.HTTPException:
            pass
        return True

    # 4) Küfür -> 7 gün mute + log
    if kufur_var_mi(icerik):
        await sil()
        await otomod_mute(message, uye, KUFUR_MUTE_SURESI, "Küfür")
        return True

    return False


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        try:
            await self.tree.sync()
        except discord.HTTPException as e:  # Komut kaydı hata verse de bot çökmesin
            print(f"Komutlar senkronize edilemedi: {e}")
        sure_kontrol.start()
        eski_kayit_temizle.start()
        mute_kontrol.start()


bot = Bot()


async def tum_sunucularda_banla(kullanici_id: int, sebep: str):
    basarisiz = []
    for g in bot.guilds:
        try:
            await g.ban(discord.Object(id=kullanici_id), reason=sebep[:500])
        except discord.HTTPException:
            basarisiz.append(g.name)
    return len(bot.guilds) - len(basarisiz), basarisiz


async def tum_sunucularda_kaldir(kullanici_id: int):
    for g in bot.guilds:
        try:
            await g.unban(discord.Object(id=kullanici_id))
        except discord.HTTPException:
            pass  # Zaten banlı değil ya da yetki yok


async def dm_dene(kisi: discord.abc.User, metin: str):
    try:
        await kisi.send(metin)
    except (discord.Forbidden, discord.HTTPException):
        pass


def ban_kontrol(interaction: discord.Interaction, kisi: discord.User):
    g = interaction.guild
    if kisi.id == interaction.user.id:
        return "Kendini banlayamazsın."
    if kisi.id == bot.user.id:
        return "Botu banlayamazsın."
    if kisi.id == g.owner_id:
        return "Sunucu sahibini banlayamazsın."
    uye = g.get_member(kisi.id)
    if uye and interaction.user.id != g.owner_id and uye.top_role >= interaction.user.top_role:
        return "Bu kişinin rolü seninkine eşit ya da yüksek, banlayamazsın."
    return None


# Süreli banların süresi dolunca otomatik kaldırır
@tasks.loop(seconds=60)
async def sure_kontrol():
    veri = banlari_yukle()
    degisti = False
    for uid, kayit in list(veri.items()):
        if not ban_aktif_mi(kayit):
            await tum_sunucularda_kaldir(int(uid))
            del veri[uid]
            degisti = True
    if degisti:
        banlari_kaydet(veri)


@sure_kontrol.before_loop
async def sure_kontrol_hazirla():
    await bot.wait_until_ready()


@bot.event
async def on_ready():
    print(f"{bot.user} olarak giriş yapıldı.")
    link = discord.utils.oauth_url(
        bot.user.id, permissions=GEREKLI_IZINLER, scopes=("bot", "applications.commands")
    )
    print("Botu sunucuya bu linkle ekle (gerekli izinleri kendisi ister):\n" + link)


# Bot yeni bir sunucuya eklenince: sahibine DM'den haber ver + aktif banları orada da uygula
@bot.event
async def on_guild_join(guild: discord.Guild):
    sahip = bot.get_user(OWNER_KULLANICI_ID)
    if sahip is None:
        try:
            sahip = await bot.fetch_user(OWNER_KULLANICI_ID)
        except discord.HTTPException:
            sahip = None
    if sahip:
        link = None
        for kanal in guild.text_channels:
            if kanal.permissions_for(guild.me).create_instant_invite:
                try:
                    link = (await kanal.create_invite(max_age=0, max_uses=0, unique=False)).url
                    break
                except discord.HTTPException:
                    continue
        metin = f"Bot yeni bir sunucuya eklendi: **{guild.name}** (ID: `{guild.id}`, {guild.member_count} üye)"
        if link:
            metin += f"\nDavet linki: {link}"
        await dm_dene(sahip, metin)

    for uid, kayit in banlari_yukle().items():
        if ban_aktif_mi(kayit):
            try:
                await guild.ban(discord.Object(id=int(uid)), reason="Ortak ban listesi")
            except discord.HTTPException:
                pass


# ---------- DM üzerinden bot kontrolü (SADECE OWNER_KULLANICI_ID) ----------
DM_YARDIM_METNI = """**DM komutları (sadece sana özel):**
`!sunucular` — botun bulunduğu sunucuları listeler
`!duyuru <sunucu_id> <kanal_id> <mesaj>` — o sunucudaki kanala duyuru atar (link olabilir)
`!mesaj <sunucu_id> <kullanici_id> <mesaj>` — o sunucudaki üyeye bot hesabından DM atar
`!ban <kullanici_id> <sure> <sebep>` — süreli ban (örn: 7g, 2sa) — botun olduğu tüm sunucularda
`!unban <kullanici_id>` — süreli banı kaldırır
`!databan <kullanici_id> <sebep>` — süresiz ban — botun olduğu tüm sunucularda
`!undataban <kullanici_id>` — süresiz banı kaldırır
`!mute <sunucu_id> <kullanici_id> <sure> <sebep>` — o sunucuda süreli susturur
`!unmute <sunucu_id> <kullanici_id>` — susturmayı kaldırır
`!clear <sunucu_id> <kanal_id> <sayi>` — o kanaldaki mesajları siler (sayı yazmazsan hepsini siler)"""


def _sayi(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def dm_hedef_kontrol(guild: discord.Guild, hedef_id: int):
    if hedef_id == bot.user.id:
        return "Botu hedef alamazsın."
    if hedef_id == guild.owner_id:
        return "Sunucu sahibini hedef alamazsın."
    return None


async def dm_komut_isle(message: discord.Message):
    parcalar = message.content.strip().split(maxsplit=1)
    komut = parcalar[0].lstrip("!").lower()
    kalan = parcalar[1] if len(parcalar) > 1 else ""

    async def soyle(m):
        await message.channel.send(m[:2000])

    if komut in ("yardim", "help"):
        return await soyle(DM_YARDIM_METNI)

    if komut == "sunucular":
        if not bot.guilds:
            return await soyle("Botun bulunduğu bir sunucu yok.")
        satirlar = [f"`{g.id}` — {g.name} ({g.member_count} üye)" for g in bot.guilds]
        return await soyle("**Botun bulunduğu sunucular:**\n" + "\n".join(satirlar))

    if komut == "duyuru":
        p = kalan.split(maxsplit=2)
        if len(p) < 3 or _sayi(p[0]) is None or _sayi(p[1]) is None:
            return await soyle("Kullanım: `!duyuru <sunucu_id> <kanal_id> <mesaj>`")
        guild = bot.get_guild(_sayi(p[0]))
        if not guild:
            return await soyle("O sunucu bulunamadı (ID'yi `!sunucular` ile kontrol et).")
        kanal = guild.get_channel(_sayi(p[1]))
        if not isinstance(kanal, discord.TextChannel):
            return await soyle("O kanal bulunamadı.")
        try:
            await kanal.send(p[2])
        except discord.Forbidden:
            return await soyle("Botun o kanala mesaj gönderme izni yok.")
        return await soyle(f"Duyuru **{guild.name}** sunucusunda {kanal.mention} kanalına gönderildi.")

    if komut == "mesaj":
        p = kalan.split(maxsplit=2)
        if len(p) < 3 or _sayi(p[0]) is None or _sayi(p[1]) is None:
            return await soyle("Kullanım: `!mesaj <sunucu_id> <kullanici_id> <mesaj>`")
        guild = bot.get_guild(_sayi(p[0]))
        if not guild:
            return await soyle("O sunucu bulunamadı.")
        uye = guild.get_member(_sayi(p[1]))
        if not uye:
            return await soyle("O kişi o sunucuda bulunamadı.")
        try:
            await uye.send(p[2])
        except discord.Forbidden:
            return await soyle("Bu kişinin DM'leri kapalı.")
        return await soyle(f"{uye} kişisine mesaj gönderildi.")

    if komut in ("ban", "databan"):
        p = kalan.split(maxsplit=(2 if komut == "ban" else 1))
        if komut == "ban":
            if len(p) < 2 or _sayi(p[0]) is None:
                return await soyle("Kullanım: `!ban <kullanici_id> <sure> <sebep>`")
            saniye = sure_coz(p[1])
            if saniye is None:
                return await soyle("Geçersiz süre. Örnekler: `30dk`, `2sa`, `7g`, `1hf`")
            sebep = p[2] if len(p) > 2 else "Sebep belirtilmedi"
        else:
            if len(p) < 1 or _sayi(p[0]) is None:
                return await soyle("Kullanım: `!databan <kullanici_id> <sebep>`")
            sebep = p[1] if len(p) > 1 else "Sebep belirtilmedi"
        hedef_id = _sayi(p[0])
        try:
            hedef = bot.get_user(hedef_id) or await bot.fetch_user(hedef_id)
        except discord.HTTPException:
            return await soyle("O kullanıcı bulunamadı.")
        veri = banlari_yukle()
        mevcut = veri.get(str(hedef_id))
        if komut == "ban" and mevcut and mevcut["bitis"] is None:
            return await soyle("Bu kişi zaten süresiz banlı. Önce `!undataban` yapmalı.")
        bitis = None if komut == "databan" else int(time.time()) + saniye
        veri[str(hedef_id)] = {"bitis": bitis, "sunucu_id": None, "sebep": sebep}
        banlari_kaydet(veri)
        await dm_dene(
            hedef,
            f"Botun olduğu sunuculardan {'süresiz' if bitis is None else 'süreli'} banlandın.\nSebep: {sebep}"
            + (f"\nBitiş: <t:{bitis}:F>" if bitis else ""),
        )
        basarili, basarisiz = await tum_sunucularda_banla(hedef_id, sebep)
        yanit = f"**{hedef}** {basarili} sunucuda banlandı."
        if bitis:
            yanit += f" Bitiş: <t:{bitis}:F>"
        if basarisiz:
            yanit += f"\nBanlanamayan sunucular: {', '.join(basarisiz)}"
        return await soyle(yanit)

    if komut in ("unban", "undataban"):
        p = kalan.split()
        if len(p) < 1 or _sayi(p[0]) is None:
            return await soyle(f"Kullanım: `!{komut} <kullanici_id>`")
        hedef_id = _sayi(p[0])
        veri = banlari_yukle()
        kayit = veri.get(str(hedef_id))
        if not kayit:
            return await soyle("Bu kişi ortak ban listesinde yok.")
        if komut == "unban" and kayit["bitis"] is None:
            return await soyle("Bu kişi süresiz banlı, `!undataban` kullan.")
        if komut == "undataban" and kayit["bitis"] is not None:
            return await soyle("Bu kişi süreli banlı, `!unban` kullan.")
        del veri[str(hedef_id)]
        banlari_kaydet(veri)
        await tum_sunucularda_kaldir(hedef_id)
        return await soyle(f"`{hedef_id}` için ban tüm sunucularda kaldırıldı.")

    if komut in ("mute", "unmute"):
        p = kalan.split(maxsplit=(3 if komut == "mute" else 1))
        if len(p) < 2 or _sayi(p[0]) is None or _sayi(p[1]) is None:
            return await soyle(f"Kullanım: `!{komut} <sunucu_id> <kullanici_id>" + (" <sure> <sebep>`" if komut == "mute" else "`"))
        guild = bot.get_guild(_sayi(p[0]))
        if not guild:
            return await soyle("O sunucu bulunamadı.")
        uye = guild.get_member(_sayi(p[1]))
        if not uye:
            return await soyle("O kişi o sunucuda bulunamadı.")
        hata = dm_hedef_kontrol(guild, uye.id)
        if hata:
            return await soyle(hata)
        try:
            if komut == "mute":
                if len(p) < 3:
                    return await soyle("Kullanım: `!mute <sunucu_id> <kullanici_id> <sure> <sebep>`")
                saniye = sure_coz(p[2])
                if saniye is None:
                    return await soyle("Geçersiz süre. Örnekler: `30dk`, `2sa`, `7g`, `1hf`")
                sebep = p[3] if len(p) > 3 else "Sebep belirtilmedi"
                bitis = await mute_uygula(uye, saniye, sebep)
                await dm_dene(uye, f"**{guild.name}** sunucusunda susturuldun.\nSebep: {sebep}\nBitiş: <t:{bitis}:F>")
                await ceza_logla(guild, "Mute (DM komutu)", uye, sebep, bitis=bitis, yetkili=message.author)
                return await soyle(f"**{uye}** susturuldu. Bitiş: <t:{bitis}:F>")
            else:
                await mute_kaldir(uye, "DM komutuyla kaldırıldı")
                await ceza_logla(guild, "Unmute (DM komutu)", uye, "Mute kaldırıldı", yetkili=message.author)
                return await soyle(f"**{uye}** için susturma kaldırıldı.")
        except discord.HTTPException:
            return await soyle("İşlem başarısız (botun izni/rol sırasını kontrol et).")

    if komut == "clear":
        p = kalan.split()
        if len(p) < 2 or _sayi(p[0]) is None or _sayi(p[1]) is None:
            return await soyle("Kullanım: `!clear <sunucu_id> <kanal_id> <sayi>` (sayı yazmazsan hepsini siler)")
        guild = bot.get_guild(_sayi(p[0]))
        if not guild:
            return await soyle("O sunucu bulunamadı.")
        kanal = guild.get_channel(_sayi(p[1]))
        if not isinstance(kanal, discord.TextChannel):
            return await soyle("O kanal bulunamadı.")
        sayi = _sayi(p[2]) if len(p) > 2 else None
        try:
            silinen = await kanal.purge(limit=sayi)
        except discord.HTTPException:
            return await soyle("Silinemedi (botun izni yok).")
        return await soyle(f"{len(silinen)} mesaj silindi.")

    await soyle("Bilinmeyen komut. `!yardim` yaz.")


# 0) !format yazılınca başvuru formatını gönderir
@bot.event
async def on_message(message: discord.Message):
    """Asıl iş _mesaj_isle'de; beklenmeyen hata olursa konsola yazar ve botu etiketleyen kişiye kısa bilgi verir."""
    try:
        await _mesaj_isle(message)
    except Exception as e:
        import traceback
        traceback.print_exc()
        if message.guild and bot.user and message.content.strip().startswith((f"<@{bot.user.id}>", f"<@!{bot.user.id}>")):
            try:
                await message.reply(f"Bir hata oldu ({type(e).__name__}), Railway Deploy Logs'a bak.", mention_author=False)
            except discord.HTTPException:
                pass


async def _mesaj_isle(message: discord.Message):
    if message.author.bot:
        return
    if message.guild is None:  # DM
        if message.author.id == OWNER_KULLANICI_ID:
            if message.content.strip().startswith("!"):
                await dm_komut_isle(message)
            return
        # Owner değilse: botun DM'ine yazdığı mesajı, kim yazdığıyla beraber owner'a ilet
        sahip = bot.get_user(OWNER_KULLANICI_ID)
        if sahip is None:
            try:
                sahip = await bot.fetch_user(OWNER_KULLANICI_ID)
            except discord.HTTPException:
                sahip = None
        if sahip:
            icerik = message.content or "*(metin yok)*"
            ekler = "\n".join(a.url for a in message.attachments)
            metin = f"**{message.author}** (`{message.author.id}`) sana DM'den yazdı:\n{icerik}"
            if ekler:
                metin += f"\n📎 {ekler}"
            await dm_dene(sahip, metin[:2000])
        return
    if message.guild:  # Mesajı kaydet (silinirse /silinmislistele ile görülebilsin)
        ekler = "\n".join(a.url for a in message.attachments)
        db.execute(
            "INSERT OR REPLACE INTO mesajlar VALUES (?,?,?,?,?,?,?,?,NULL)",
            (message.id, message.guild.id, message.channel.id, message.author.id,
             message.author.display_name, message.content, ekler, int(message.created_at.timestamp())),
        )
        db.commit()
        if await otomod(message):  # Küfür / link / spam kontrolü
            return
    if message.content.strip().lower() == "!format":
        await message.channel.send(FORMAT_METNI)
        return
    if message.content.strip().lower() == "!linkler":
        await message.channel.send(LINKLER_METNI)
        return

    # Mesaj botun etiketiyle BAŞLIYORSA yapay zeka cevap versin
    etiketler = (f"<@{bot.user.id}>", f"<@!{bot.user.id}>")
    icerik = message.content.strip()
    if icerik.startswith(etiketler):
        soru = icerik
        for e in etiketler:
            soru = soru.replace(e, "", 1)
        soru = soru.strip()
        if not soru:
            return
        # "@bot öğren: ..." -> sadece sahip/yöneticiler bota bilgi öğretebilir
        ogren_m = re.match(r"(?is)^(?:öğren|ogren|ezberle)(?:\s*:\s*|\s+)(.+)$", soru)
        if ogren_m and (message.author.id == OWNER_KULLANICI_ID or message.author.guild_permissions.administrator):
            bilgi = ogren_m.group(1).strip()
            adet = ogrenilen_ekle(message.guild.id, bilgi)
            await message.reply(f"Tamam, ezberledim. (Toplam {adet} bilgi biliyorum)", mention_author=False)
            await ogrenme_logla(message.guild, f"📚 **#{adet}** öğrenildi ({message.author.display_name}): {bilgi[:500]}")
            return
        # "@bot yap: ..." -> sadece sunucu sahibi / bot sahibi sunucuda işlem yaptırabilir
        yap_m = re.match(r"(?is)^yap\s*:\s*(.+)$", soru)
        if yap_m and yap_yetkilisi_mi(message.author.id, message.guild):
            async with message.channel.typing():
                metin, gorunum = await yap_metni_ve_gorunum(
                    message.guild, yap_m.group(1).strip(), message.channel, await sohbet_baglami(message))
            await message.reply(metin, view=gorunum, mention_author=False)
            return
        # Sunucu sahibi "yap:" yazmadan da bir şey isterse: yönetim isteği mi diye bak, öyleyse onaya sun
        if not yap_m and yap_yetkilisi_mi(message.author.id, message.guild) and yonetim_gibi_mi(soru):
            async with message.channel.typing():
                islemler, not_ = await yap_hazirla(message.guild, soru, message.channel, await sohbet_baglami(message))
            if islemler:
                sorgular, degisiklikler = sorgu_ayir(message.guild, islemler)
                metin = "\n".join(sorgular)
                gorunum = None
                if degisiklikler:
                    satirlar = "\n".join(f"{n}. {t_}" for n, t_ in enumerate(yap_tanimlar(message.guild, degisiklikler), 1))
                    metin += ("\n\n" if metin else "") + f"Şunları yapacağım:\n{satirlar}\n" + (f"\nNot: {not_}\n" if not_ else "") + "\nOnaylıyor musun?"
                    gorunum = YapOnay(message.guild, degisiklikler, {OWNER_KULLANICI_ID, message.guild.owner_id})
                elif not_:
                    metin += f"\nNot: {not_}"
                await message.reply(metin[:1900], view=gorunum, mention_author=False)
                return
            if islemler is not None and not_:
                await message.reply(f"Bunu yapamıyorum: {not_}"[:1900], mention_author=False)
                return
            # Yönetim isteği değil (ya da yapay zeka cevap vermedi) -> aşağıda normal sohbet cevabı
        # Kota koruma: aynı kişi çok sık sorarsa API'ye gitmeden ⏳ ile işaretle
        simdi = time.time()
        if simdi - ai_son_kullanim.get(message.author.id, 0) < AI_BEKLEME_SANIYE:
            try:
                await message.add_reaction("⏳")
            except discord.HTTPException:
                pass
            return
        ai_son_kullanim[message.author.id] = simdi
        async with message.channel.typing():
            baglam = await sohbet_baglami(message)
            ilgili = gecmis_ara(message.guild.id, soru)
            if ilgili:
                baglam += "\n\nSunucuda geçmişte yazılmış ilgili mesajlar:\n" + "\n".join(ilgili)
            cevap = await gemini_cevap(soru, baglam, ai_sistem_prompt(message.guild.id))
        await message.reply(cevap, mention_author=False)


# 0b) Yeni açılan ticket kanalına otomatik !format metnini atar
@bot.event
async def on_guild_channel_create(kanal: discord.abc.GuildChannel):
    if not isinstance(kanal, discord.TextChannel):
        return
    if not any(a in kanal.name.lower() for a in TICKET_KANAL_ANAHTARLARI):
        return
    await asyncio.sleep(3)  # Ticket botu kanalı kurup izinleri ayarlamayı bitirsin
    try:
        await kanal.send(FORMAT_METNI)
    except discord.HTTPException:
        pass  # Botun bu kanalı görme/yazma izni yoksa sessizce geç


# 1) Yeni üye gelince: banlıysa engelle, değilse hoş geldin mesajı
@bot.event
async def on_member_join(member: discord.Member):
    kayit = banlari_yukle().get(str(member.id))
    if kayit and ban_aktif_mi(kayit):
        try:
            await member.guild.ban(member, reason="Ortak ban listesi")
        except discord.HTTPException:
            pass
        return

    kanal = discord.utils.get(member.guild.text_channels, name=HOSGELDIN_KANAL_ADI)
    if kanal:
        await kanal.send(f"Sunucuya hoş geldin {member.mention}! 🎉")

    sohbet = member.guild.get_channel(SOHBET_KANAL_ID)
    if sohbet:
        try:
            await sohbet.send(f"{member.mention} {HOSGELDIN_METNI}")
        except discord.HTTPException:
            pass

    await dm_dene(member, HOSGELDIN_METNI)


# 2) /davet -> arkadaşların için süreli, sınırlı davet linki üretir
@bot.tree.command(name="davet", description="Arkadaşların için davet linki oluşturur")
@app_commands.describe(kullanim="Kaç kişi kullanabilsin", saat="Kaç saat geçerli olsun")
async def davet(interaction: discord.Interaction, kullanim: int = 5, saat: int = 24):
    if not interaction.guild:
        return await interaction.response.send_message("Bu komut sadece sunucuda çalışır.", ephemeral=True)
    link = await interaction.channel.create_invite(max_uses=kullanim, max_age=saat * 3600, unique=True)
    await interaction.response.send_message(
        f"Davet linkin ({kullanim} kullanım, {saat} saat geçerli):\n{link.url}", ephemeral=True
    )


# 3) /mesaj -> SADECE sunucu sahibi. Sunucudaki bir üyeye botun hesabından DM atar
@bot.tree.command(name="mesaj", description="Sunucudaki bir üyeye bot hesabından özel mesaj gönderir (sadece sunucu sahibi)")
@app_commands.describe(metin="Mesaj", kisi="Mesajın gideceği kişi (herkes seçiliyse boş bırak)",
                       herkes="Evet seçersen sunucudaki tüm üyelere (botlar hariç) gider")
@app_commands.choices(herkes=[app_commands.Choice(name="Evet, herkese gönder", value=1)])
@app_commands.guild_only()
async def mesaj(interaction: discord.Interaction, metin: str, kisi: discord.Member = None,
                herkes: app_commands.Choice[int] = None):
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(
            "`/mesaj` komutunu sadece **sunucu sahibi** kullanabilir.", ephemeral=True
        )
    if herkes:
        hedefler = [m for m in interaction.guild.members if not m.bot and m.id != interaction.user.id]
        sure_dk = len(hedefler)  # her DM arası 1 dakika

        class Onay(discord.ui.View):
            def __init__(self):
                super().__init__(timeout=60)

            @discord.ui.button(label="Evet, herkese gönder", style=discord.ButtonStyle.danger)
            async def evet(self, inter: discord.Interaction, button: discord.ui.Button):
                if inter.user.id != interaction.guild.owner_id:
                    return await inter.response.send_message("Bu buton sana ait değil.", ephemeral=True)
                self.stop()
                await inter.response.edit_message(
                    content="Gönderim başladı. Her DM arasında 1 dakika var, bitince sana DM'den haber vereceğim.", view=None)
                sahip = inter.user

                async def gonder():
                    basarili = kapali = 0
                    for m in hedefler:
                        try:
                            await m.send(metin)
                            basarili += 1
                        except (discord.Forbidden, discord.HTTPException):
                            kapali += 1
                        await asyncio.sleep(60)  # DM'ler arası 1 dakika
                    try:
                        await sahip.send(
                            f"Toplu mesaj bitti. Gönderilen: **{basarili}**, gönderilemeyen (DM kapalı vb.): **{kapali}**")
                    except discord.HTTPException:
                        pass

                asyncio.create_task(gonder())

            @discord.ui.button(label="İptal", style=discord.ButtonStyle.secondary)
            async def iptal(self, inter: discord.Interaction, button: discord.ui.Button):
                self.stop()
                await inter.response.edit_message(content="İptal edildi.", view=None)

        return await interaction.response.send_message(
            f"**{len(hedefler)}** kişiye şu mesaj gidecek (yaklaşık {sure_dk} dk sürer):\n\n{metin}\n\nOnaylıyor musun?",
            view=Onay(), ephemeral=True
        )
    if kisi is None:
        return await interaction.response.send_message("Bir kişi seç ya da `herkes` seçeneğini kullan.", ephemeral=True)
    try:
        await kisi.send(metin)  # Mesaj botun hesabından, olduğu gibi gider
        await interaction.response.send_message(f"{kisi.display_name} kişisine mesaj gönderildi.", ephemeral=True)
    except discord.Forbidden:
        await interaction.response.send_message("Bu kişinin DM'leri kapalı.", ephemeral=True)


# 4b) /ogrendiklerim, /unut, /yap -> yapay zeka bilgi ve yönetim komutları
@bot.tree.command(name="ogrendiklerim", description="Bota öğretilen bilgileri listeler (yönetici)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def ogrendiklerim(interaction: discord.Interaction):
    bilgiler = ogrenilenleri_getir(interaction.guild.id)
    if not bilgiler:
        return await interaction.response.send_message(
            "Henüz bir şey öğretilmedi. Bota `@bot öğren: ...` yazarak öğretebilirsin.", ephemeral=True)
    metin = "\n".join(f"{n}. {b[:150]}" for n, b in enumerate(bilgiler, 1))
    await interaction.response.send_message(metin[:1900], ephemeral=True)


@bot.tree.command(name="unut", description="Bota öğretilen bir bilgiyi siler (yönetici)")
@app_commands.describe(numara="/ogrendiklerim listesindeki numara")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def unut(interaction: discord.Interaction, numara: int):
    veri = json_yukle(OGRENILEN_DOSYASI)
    liste = veri.get(str(interaction.guild.id), [])
    if not 1 <= numara <= len(liste):
        return await interaction.response.send_message("Böyle bir numara yok.", ephemeral=True)
    silinen = liste.pop(numara - 1)
    veri[str(interaction.guild.id)] = liste
    json_kaydet(OGRENILEN_DOSYASI, veri)
    await interaction.response.send_message(f"Unuttum: {silinen[:150]}", ephemeral=True)
    await ogrenme_logla(interaction.guild, f"🗑️ **#{numara}** unutuldu ({interaction.user.display_name}): {silinen[:500]}\n"
                        f"(Sonraki numaralar bir azaldı, güncel liste için `/ogrendiklerim`)")


@bot.tree.command(name="öğrenmebelirle", description="Botun öğrendiği bilgilerin numaralı log atılacağı kanalı seçer (yönetici)")
@app_commands.describe(kanal="Log kanalı")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def ogrenmebelirle(interaction: discord.Interaction, kanal: discord.TextChannel):
    if not kanal.permissions_for(interaction.guild.me).send_messages:
        return await interaction.response.send_message(f"Botun {kanal.mention} kanalına yazma izni yok.", ephemeral=True)
    ayarlar = json_yukle(AYAR_DOSYASI)
    ayarlar.setdefault(str(interaction.guild.id), {})["ogrenme_log_kanal"] = kanal.id
    json_kaydet(AYAR_DOSYASI, ayarlar)
    await interaction.response.send_message(f"Öğrenilen bilgiler artık {kanal.mention} kanalına numarasıyla loglanacak.", ephemeral=True)


@bot.tree.command(name="yap", description="Yapay zekaya sunucuda iş yaptır (sadece sunucu sahibi)")
@app_commands.describe(istek="Örn: Üye rolü #duyurular kanalını görsün ama yazamasın")
@app_commands.guild_only()
async def yap(interaction: discord.Interaction, istek: str):
    if not yap_yetkilisi_mi(interaction.user.id, interaction.guild):
        return await interaction.response.send_message("Bu komutu sadece sunucu sahibi kullanabilir.", ephemeral=True)
    await interaction.response.defer(ephemeral=True)
    metin, gorunum = await yap_metni_ve_gorunum(interaction.guild, istek, interaction.channel)
    if gorunum:
        await interaction.followup.send(metin, view=gorunum, ephemeral=True)
    else:
        await interaction.followup.send(metin, ephemeral=True)



# 4c) /sertmesaj, /normalmesaj -> yapay zekanın üslubunu değiştirir (sadece sunucu sahibi)
async def ai_mod_ayarla(interaction: discord.Interaction, mod: str, cevap: str):
    if not yap_yetkilisi_mi(interaction.user.id, interaction.guild):
        return await interaction.response.send_message("Bu komutu sadece sunucu sahibi kullanabilir.", ephemeral=True)
    ayarlar = json_yukle(AYAR_DOSYASI)
    ayarlar.setdefault(str(interaction.guild.id), {})["ai_mod"] = mod
    json_kaydet(AYAR_DOSYASI, ayarlar)
    await interaction.response.send_message(cevap, ephemeral=True)


@bot.tree.command(name="sertmesaj", description="Yapay zekayı sert/agresif moda geçirir (sadece sunucu sahibi)")
@app_commands.guild_only()
async def sertmesaj(interaction: discord.Interaction):
    await ai_mod_ayarla(interaction, "sert", "Yapay zeka artık **sert** modda.")


@bot.tree.command(name="normalmesaj", description="Yapay zekayı normal (esprili) moda geçirir (sadece sunucu sahibi)")
@app_commands.guild_only()
async def normalmesaj(interaction: discord.Interaction):
    await ai_mod_ayarla(interaction, "normal", "Yapay zeka artık **normal** modda.")


# 4) /duyuru -> sadece yöneticiler, bulunduğu kanala embed duyuru atar
@bot.tree.command(name="duyuru", description="Seçtiğin kanala duyuru gönderir (yönetici, link olabilir)")
@app_commands.describe(kanal="Duyurunun atılacağı kanal", metin="Duyuru metni (link olabilir)", baslik="Başlık (opsiyonel)")
@app_commands.default_permissions(administrator=True)
async def duyuru(interaction: discord.Interaction, kanal: discord.TextChannel, metin: str, baslik: str = None):
    if not kanal.permissions_for(interaction.guild.me).send_messages:
        return await interaction.response.send_message(f"Botun {kanal.mention} kanalına mesaj gönderme izni yok.", ephemeral=True)
    if baslik:
        embed = discord.Embed(title=baslik, description=metin, color=discord.Color.blurple())
        embed.set_footer(text=f"Duyuruyu yapan: {interaction.user.display_name}")
        await kanal.send(embed=embed)
    else:
        await kanal.send(metin)  # Başlıksız: linkler Discord'da otomatik önizlenir
    await interaction.response.send_message(f"Duyuru {kanal.mention} kanalına gönderildi.", ephemeral=True)


# 5) /clear -> kanaldaki mesajları siler (sayı verilmezse hepsini siler)
@bot.tree.command(name="clear", description="Kanaldaki mesajları siler (sayı yazmazsan hepsini siler)")
@app_commands.describe(sayi="Silinecek mesaj sayısı (boş bırakırsan kanaldaki tüm mesajlar silinir)")
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
async def clear(interaction: discord.Interaction, sayi: app_commands.Range[int, 1, 10000] = None):
    await interaction.response.defer(ephemeral=True)  # Silme uzun sürebilir
    def kaydet(m: discord.Message) -> bool:
        clear_idleri.add(m.id)  # Bu mesajlar yetkili tarafından silindi, listeye girmesin
        return True

    silinen = await interaction.channel.purge(limit=sayi, check=kaydet)  # limit=None -> hepsi
    await interaction.followup.send(f"{len(silinen)} mesaj silindi.", ephemeral=True)


@clear.error
async def clear_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Mesajları Yönet")


# 5b) /silkişi -> seçilen kişinin bu kanaldaki mesajlarını siler (sayı verilmezse hepsini)
SILKISI_TARAMA_LIMITI = 20000  # Bir kanalda en fazla bu kadar eski mesaja kadar tarar


@bot.tree.command(name="silkişi", description="Seçtiğin kişinin bu kanaldaki mesajlarını siler (sayı yazmazsan hepsini)")
@app_commands.describe(kisi="Mesajları silinecek kişi (sunucudan ayrıldıysa ID yapıştır)",
                       sayi="Kaç mesajı silinsin (boş bırakırsan bu kanaldaki tüm mesajları silinir)")
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
@app_commands.checks.bot_has_permissions(manage_messages=True, read_message_history=True)
async def silkisi(interaction: discord.Interaction, kisi: discord.User, sayi: app_commands.Range[int, 1, 10000] = None):
    await interaction.response.defer(ephemeral=True)  # Silme uzun sürebilir
    kanal = interaction.channel

    # En yeniden eskiye doğru kişinin mesajlarını topla (sayi varsa o kadar, yoksa hepsi)
    hedefler = []
    async for m in kanal.history(limit=SILKISI_TARAMA_LIMITI):
        if m.author.id == kisi.id:
            hedefler.append(m)
            if sayi and len(hedefler) >= sayi:
                break

    if not hedefler:
        return await interaction.followup.send(f"**{kisi}** kişisinin bu kanalda silinecek mesajı bulunamadı.", ephemeral=True)

    for m in hedefler:
        clear_idleri.add(m.id)  # Yetkili sildi, "silinmiş mesajlar" listesine girmesin

    simdi = discord.utils.utcnow()
    yeni = [m for m in hedefler if (simdi - m.created_at).days < 14]  # Toplu silinebilenler
    eski = [m for m in hedefler if (simdi - m.created_at).days >= 14]  # Tek tek silinecekler
    silinen = 0
    try:
        for i in range(0, len(yeni), 100):
            parca = yeni[i:i + 100]
            if len(parca) == 1:
                await parca[0].delete()
            else:
                await kanal.delete_messages(parca)
            silinen += len(parca)
        for m in eski:
            await m.delete()
            silinen += 1
            await asyncio.sleep(0.4)  # Discord hız sınırına takılmamak için
    except discord.HTTPException:
        await interaction.followup.send(
            f"{silinen} mesaj silindi, ama işlem bir hata yüzünden yarıda kaldı (botun izni/hız sınırı).", ephemeral=True
        )
        return
    await interaction.followup.send(f"**{kisi}** kişisinin {silinen} mesajı silindi.", ephemeral=True)


@silkisi.error
async def silkisi_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Mesajları Yönet")


async def izin_hatasi(interaction: discord.Interaction, error, izin_adi: str):
    if isinstance(error, app_commands.MissingPermissions):
        msg = f"Bu komut için **{izin_adi}** iznin olmalı."
    elif isinstance(error, app_commands.BotMissingPermissions):
        msg = f"Botun bu sunucuda gerekli izinleri yok ({izin_adi} vb.)."
    else:
        msg = f"Bir hata oluştu: {error}"
    if interaction.response.is_done():
        await interaction.followup.send(msg, ephemeral=True)
    else:
        await interaction.response.send_message(msg, ephemeral=True)


# 6) /ban -> SÜRELİ ban. Süre dolana kadar botun olduğu hiçbir sunucuya giremez
@bot.tree.command(name="ban", description="Süreli ban: süre boyunca botun olduğu hiçbir sunucuya giremez")
@app_commands.describe(
    kisi="Banlanacak kişi (sunucuda değilse kullanıcı ID'sini yapıştır)",
    sure="Süre: 30dk, 2sa, 7g, 1hf gibi",
    sebep="Ban sebebi",
)
@app_commands.guild_only()
@app_commands.default_permissions(ban_members=True)
@app_commands.checks.has_permissions(ban_members=True)
@app_commands.checks.bot_has_permissions(ban_members=True)
async def ban(interaction: discord.Interaction, kisi: discord.User, sure: str, sebep: str = "Sebep belirtilmedi"):
    saniye = sure_coz(sure)
    if saniye is None:
        return await interaction.response.send_message(
            "Geçersiz süre. Örnekler: `30dk`, `2sa`, `7g`, `1hf`", ephemeral=True
        )
    hata = ban_kontrol(interaction, kisi)
    if hata:
        return await interaction.response.send_message(hata, ephemeral=True)

    veri = banlari_yukle()
    mevcut = veri.get(str(kisi.id))
    if mevcut and mevcut["bitis"] is None:
        return await interaction.response.send_message(
            "Bu kişi zaten süresiz banlı (/databan). Önce sunucu sahibi `/undataban` yapmalı.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    bitis = int(time.time()) + saniye
    veri[str(kisi.id)] = {"bitis": bitis, "sunucu_id": interaction.guild.id, "sebep": sebep}
    banlari_kaydet(veri)

    await dm_dene(kisi, f"**{interaction.guild.name}** sunucusunda süreli banlandın.\nSebep: {sebep}\nBitiş: <t:{bitis}:F>")
    basarili, basarisiz = await tum_sunucularda_banla(kisi.id, f"{interaction.user}: {sebep} (bitiş: <t:{bitis}:f>)")
    await ceza_logla(interaction.guild, "Süreli Ban", kisi, sebep, bitis=bitis, yetkili=interaction.user)

    mesaj_ = f"**{kisi}** {basarili} sunucuda banlandı. Bitiş: <t:{bitis}:F> (<t:{bitis}:R>)"
    if basarisiz:
        mesaj_ += f"\nBanlanamayan sunucular (botun izni/rol sırasını kontrol et): {', '.join(basarisiz)}"
    await interaction.followup.send(mesaj_, ephemeral=True)


@ban.error
async def ban_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Üyeleri Yasakla")


# 7) /databan -> SÜRESİZ ban. Sadece sunucu sahibi atabilir ve /undataban ile kaldırabilir.
#    Kişi sunucuda olmasa da, süreli banı olsa da ban işler (süreli ban süresize çevrilir).
@bot.tree.command(name="databan", description="Süresiz ban (sadece sunucu sahibi): botun olduğu hiçbir sunucuya giremez")
@app_commands.describe(
    kisi="Banlanacak kişi (sunucuda değilse kullanıcı ID'sini yapıştır)",
    sebep="Ban sebebi",
)
@app_commands.guild_only()
@app_commands.checks.bot_has_permissions(ban_members=True)
async def databan(interaction: discord.Interaction, kisi: discord.User, sebep: str = "Sebep belirtilmedi"):
    # Sadece sunucu sahibi kullanabilir
    if interaction.user.id != interaction.guild.owner_id:
        return await interaction.response.send_message(
            "`/databan` komutunu sadece **sunucu sahibi** kullanabilir.", ephemeral=True
        )
    hata = ban_kontrol(interaction, kisi)
    if hata:
        return await interaction.response.send_message(hata, ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    veri = banlari_yukle()
    veri[str(kisi.id)] = {"bitis": None, "sunucu_id": interaction.guild.id, "sebep": sebep}
    banlari_kaydet(veri)

    await dm_dene(kisi, f"**{interaction.guild.name}** sunucusunda süresiz banlandın.\nSebep: {sebep}")
    basarili, basarisiz = await tum_sunucularda_banla(kisi.id, f"{interaction.user}: {sebep} (süresiz)")
    await ceza_logla(interaction.guild, "Süresiz Ban (databan)", kisi, sebep, yetkili=interaction.user)

    mesaj_ = f"**{kisi}** {basarili} sunucuda süresiz banlandı. Banı sadece sunucu sahibi `/undataban` ile kaldırabilir."
    if basarisiz:
        mesaj_ += f"\nBanlanamayan sunucular (botun izni/rol sırasını kontrol et): {', '.join(basarisiz)}"
    await interaction.followup.send(mesaj_, ephemeral=True)


@databan.error
async def databan_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Üyeleri Yasakla")


# 8) /unban -> SÜRELİ banı kaldırır (Üyeleri Yasakla izni olan herkes kullanabilir)
@bot.tree.command(name="unban", description="Süreli banı kaldırır")
@app_commands.describe(kisi="Banı kaldırılacak kişi (kullanıcı ID'sini yapıştırabilirsin)")
@app_commands.guild_only()
@app_commands.default_permissions(ban_members=True)
@app_commands.checks.has_permissions(ban_members=True)
async def unban(interaction: discord.Interaction, kisi: discord.User):
    veri = banlari_yukle()
    kayit = veri.get(str(kisi.id))
    if not kayit:
        # Ortak listede yok: bu sunucudaki normal ban ya da otomod banı olabilir
        await interaction.response.defer(ephemeral=True)
        try:
            await interaction.guild.unban(kisi, reason=f"{interaction.user} tarafından kaldırıldı")
        except discord.NotFound:
            return await interaction.followup.send("Bu kişi bu sunucuda banlı değil.", ephemeral=True)
        except discord.HTTPException:
            return await interaction.followup.send("Ban kaldırılamadı (botun Üyeleri Yasakla izni var mı?).", ephemeral=True)
        await ceza_logla(interaction.guild, "Unban", kisi, "Ban kaldırıldı", yetkili=interaction.user)
        return await interaction.followup.send(f"**{kisi}** için bu sunucudaki ban kaldırıldı.", ephemeral=True)
    if kayit["bitis"] is None:
        return await interaction.response.send_message(
            "Bu kişi **süresiz** banlı (/databan). Kaldırmak için sunucu sahibi `/undataban` kullanmalı.",
            ephemeral=True,
        )

    await interaction.response.defer(ephemeral=True)
    del veri[str(kisi.id)]
    banlari_kaydet(veri)
    await tum_sunucularda_kaldir(kisi.id)
    await ceza_logla(interaction.guild, "Unban (süreli ban kaldırıldı)", kisi, "Ban kaldırıldı", yetkili=interaction.user)
    await interaction.followup.send(f"**{kisi}** için süreli ban tüm sunucularda kaldırıldı.", ephemeral=True)


@unban.error
async def unban_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Üyeleri Yasakla")


# 8b) /undataban -> SÜRESİZ banı kaldırır. SADECE banı koyan sunucunun sahibi kullanabilir
@bot.tree.command(name="undataban", description="Süresiz banı kaldırır (sadece sunucu sahibi)")
@app_commands.describe(kisi="Süresiz banı kaldırılacak kişi (kullanıcı ID'sini yapıştırabilirsin)")
@app_commands.guild_only()
async def undataban(interaction: discord.Interaction, kisi: discord.User):
    veri = banlari_yukle()
    kayit = veri.get(str(kisi.id))
    if not kayit:
        return await interaction.response.send_message("Bu kişi ortak ban listesinde yok.", ephemeral=True)
    if kayit["bitis"] is not None:
        return await interaction.response.send_message(
            "Bu kişinin banı süreli. Kaldırmak için `/unban` kullan.", ephemeral=True
        )

    ban_sunucusu = bot.get_guild(kayit["sunucu_id"])
    sahip_id = ban_sunucusu.owner_id if ban_sunucusu else interaction.guild.owner_id
    if interaction.user.id != sahip_id:
        return await interaction.response.send_message(
            "Süresiz banı sadece bu banı koyan sunucunun **sahibi** kaldırabilir.", ephemeral=True
        )

    await interaction.response.defer(ephemeral=True)
    del veri[str(kisi.id)]
    banlari_kaydet(veri)
    await tum_sunucularda_kaldir(kisi.id)
    await ceza_logla(interaction.guild, "Undataban (süresiz ban kaldırıldı)", kisi, "Ban kaldırıldı", yetkili=interaction.user)
    await interaction.followup.send(f"**{kisi}** için süresiz ban tüm sunucularda kaldırıldı.", ephemeral=True)


# 9) Silinen mesajları kaydet + /silinmislistele (sadece yetkililer)
def silindi_isaretle(mesaj_idleri):
    simdi = int(time.time())
    for mid in mesaj_idleri:
        if mid in clear_idleri:  # /clear ile silinmiş, listeye alma
            clear_idleri.discard(mid)
            continue
        db.execute("UPDATE mesajlar SET silinme=? WHERE id=? AND silinme IS NULL", (simdi, mid))
    db.commit()


@bot.event
async def on_raw_message_delete(payload: discord.RawMessageDeleteEvent):
    if payload.guild_id:
        silindi_isaretle([payload.message_id])


@bot.event
async def on_raw_bulk_message_delete(payload: discord.RawBulkMessageDeleteEvent):
    if payload.guild_id:
        silindi_isaretle(payload.message_ids)


@tasks.loop(hours=1)
async def eski_kayit_temizle():
    sinir = int(time.time()) - MESAJ_SAKLAMA_GUN * 86400
    db.execute("DELETE FROM mesajlar WHERE yazilma < ?", (sinir,))
    db.commit()


@bot.tree.command(name="silinmislistele", description="Silinen mesajları listeler (sadece yetkililer)")
@app_commands.describe(
    sayi="Kaç mesaj listelensin (1-25)",
    kisi="Sadece bu kişinin silinen mesajlarını göster (isteğe bağlı)",
)
@app_commands.guild_only()
@app_commands.default_permissions(manage_messages=True)
@app_commands.checks.has_permissions(manage_messages=True)
async def silinmislistele(
    interaction: discord.Interaction,
    sayi: app_commands.Range[int, 1, 25] = 10,
    kisi: discord.User = None,
):
    sorgu = ("SELECT yazar_id, yazar_ad, kanal_id, icerik, ekler, silinme FROM mesajlar "
             "WHERE sunucu_id=? AND silinme IS NOT NULL")
    parametre = [interaction.guild.id]
    if kisi:
        sorgu += " AND yazar_id=?"
        parametre.append(kisi.id)
    sorgu += " ORDER BY silinme DESC LIMIT ?"
    parametre.append(sayi)
    satirlar = db.execute(sorgu, parametre).fetchall()

    if not satirlar:
        return await interaction.response.send_message("Kayıtlı silinmiş mesaj bulunamadı.", ephemeral=True)

    parcalar = []
    for yazar_id, ad, kanal_id, icerik, ekler, silinme in satirlar:
        metin = icerik if icerik else "*(metin yok)*"
        if len(metin) > 300:
            metin = metin[:300] + "…"
        satir = f"**{ad}** (<@{yazar_id}>) • <#{kanal_id}> • silindi <t:{silinme}:R>\n> " + metin.replace("\n", "\n> ")
        if ekler:
            satir += f"\n📎 {ekler}"
        parcalar.append(satir)

    aciklama = "\n\n".join(parcalar)
    if len(aciklama) > 4000:
        aciklama = aciklama[:4000] + "…"
    embed = discord.Embed(
        title=f"Silinen son {len(satirlar)} mesaj", description=aciklama, color=discord.Color.red()
    )
    await interaction.response.send_message(embed=embed, ephemeral=True)


@silinmislistele.error
async def silinmislistele_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Mesajları Yönet")


# 10) /mute, /unmute (Üyeleri Zaman Aşımına Uğrat izni olanlar)
def mute_engeli(interaction: discord.Interaction, kisi: discord.Member):
    g = interaction.guild
    if kisi.id == interaction.user.id:
        return "Kendini susturamazsın."
    if kisi.id == bot.user.id:
        return "Botu susturamazsın."
    if kisi.id == g.owner_id:
        return "Sunucu sahibini susturamazsın."
    if interaction.user.id != g.owner_id and kisi.top_role >= interaction.user.top_role:
        return "Bu kişinin rolü seninkine eşit ya da yüksek, susturamazsın."
    return None


@bot.tree.command(name="mute", description="Üyeyi belirtilen süre boyunca susturur")
@app_commands.describe(kisi="Susturulacak kişi", sure="Süre: 30dk, 2sa, 7g, 1hf gibi", sebep="Sebep")
@app_commands.guild_only()
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def mute(interaction: discord.Interaction, kisi: discord.Member, sure: str, sebep: str = "Sebep belirtilmedi"):
    saniye = sure_coz(sure)
    if saniye is None:
        return await interaction.response.send_message(
            "Geçersiz süre. Örnekler: `30dk`, `2sa`, `7g`, `1hf`", ephemeral=True
        )
    hata = mute_engeli(interaction, kisi)
    if hata:
        return await interaction.response.send_message(hata, ephemeral=True)

    await interaction.response.defer(ephemeral=True)
    try:
        bitis = await mute_uygula(kisi, saniye, f"{interaction.user}: {sebep}")
    except discord.HTTPException:
        return await interaction.followup.send(
            "Susturulamadı. Botun rolü, kişinin rolünün üstünde olmalı ve **Üyeleri Zaman Aşımına Uğrat** izni olmalı "
            "(yöneticiler susturulamaz).", ephemeral=True)
    await dm_dene(kisi, f"**{interaction.guild.name}** sunucusunda susturuldun.\nSebep: {sebep}\nBitiş: <t:{bitis}:F>")
    await ceza_logla(interaction.guild, "Mute", kisi, sebep, bitis=bitis, yetkili=interaction.user)
    await interaction.followup.send(
        f"**{kisi.display_name}** susturuldu. Bitiş: <t:{bitis}:F> (<t:{bitis}:R>)", ephemeral=True
    )


@mute.error
async def mute_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Üyeleri Zaman Aşımına Uğrat")


@bot.tree.command(name="unmute", description="Susturmayı kaldırır")
@app_commands.describe(kisi="Susturması kaldırılacak kişi")
@app_commands.guild_only()
@app_commands.default_permissions(moderate_members=True)
@app_commands.checks.has_permissions(moderate_members=True)
@app_commands.checks.bot_has_permissions(moderate_members=True)
async def unmute(interaction: discord.Interaction, kisi: discord.Member):
    await interaction.response.defer(ephemeral=True)
    try:
        await mute_kaldir(kisi, f"{interaction.user} tarafından kaldırıldı")
    except discord.HTTPException:
        return await interaction.followup.send("Susturma kaldırılamadı (botun izni/rol sırasını kontrol et).", ephemeral=True)
    await ceza_logla(interaction.guild, "Unmute", kisi, "Mute kaldırıldı", yetkili=interaction.user)
    await interaction.followup.send(f"**{kisi.display_name}** için susturma kaldırıldı.", ephemeral=True)


@unmute.error
async def unmute_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Üyeleri Zaman Aşımına Uğrat")


# 11) Uzun mute'ları (28 günden fazla) sürdürme + elle kaldırılan mute'ları takip etme
@tasks.loop(minutes=5)
async def mute_kontrol():
    kayitlar = json_yukle(MUTE_DOSYASI)
    simdi = int(time.time())
    degisti = False
    for anahtar, k in list(kayitlar.items()):
        if k["bitis"] <= simdi:
            del kayitlar[anahtar]
            degisti = True
            continue
        # Mevcut zaman aşımı parçası bitmek üzere ve mute daha bitmedi -> uzat
        if k["bitis"] > k["kesim"] and k["kesim"] - simdi < 900:
            gid, uid = (int(x) for x in anahtar.split(":"))
            guild = bot.get_guild(gid)
            uye = guild.get_member(uid) if guild else None
            if uye:
                parca = min(k["bitis"] - simdi, MAKS_TIMEOUT)
                try:
                    await uye.timeout(timedelta(seconds=parca), reason="Mute devam ediyor")
                    k["kesim"] = simdi + parca
                    degisti = True
                except discord.HTTPException:
                    pass
    if degisti:
        json_kaydet(MUTE_DOSYASI, kayitlar)


@mute_kontrol.before_loop
async def mute_kontrol_hazirla():
    await bot.wait_until_ready()


@bot.event
async def on_member_update(before: discord.Member, after: discord.Member):
    # Biri zaman aşımını Discord'dan elle kaldırdıysa kaydı sil (bot yeniden uygulamasın)
    if before.timed_out_until and not after.timed_out_until:
        anahtar = f"{after.guild.id}:{after.id}"
        kayitlar = json_yukle(MUTE_DOSYASI)
        k = kayitlar.get(anahtar)
        if k and time.time() < k["kesim"] - 120:
            del kayitlar[anahtar]
            json_kaydet(MUTE_DOSYASI, kayitlar)


# 12) /botcezakanal ve /cezakanal -> log kanallarını belirler (Sunucuyu Yönet izni)
async def log_kanali_ayarla(interaction: discord.Interaction, anahtar: str, kanal: discord.TextChannel, aciklama: str):
    if not kanal.permissions_for(interaction.guild.me).send_messages:
        return await interaction.response.send_message(
            f"Botun {kanal.mention} kanalına mesaj gönderme izni yok. Önce izni ver, sonra tekrar dene.", ephemeral=True
        )
    ayarlar = json_yukle(AYAR_DOSYASI)
    ayarlar.setdefault(str(interaction.guild.id), {})[anahtar] = kanal.id
    json_kaydet(AYAR_DOSYASI, ayarlar)
    await interaction.response.send_message(f"{aciklama} artık {kanal.mention} kanalına gidecek.", ephemeral=True)


@bot.tree.command(name="botcezakanal", description="Otomod (küfür/link/spam) cezalarının loglanacağı kanalı belirler")
@app_commands.describe(kanal="Log kanalı")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def botcezakanal(interaction: discord.Interaction, kanal: discord.TextChannel):
    await log_kanali_ayarla(interaction, "botcezakanal", kanal, "Otomod ceza logları")


@botcezakanal.error
async def botcezakanal_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Sunucuyu Yönet")


@bot.tree.command(name="cezakanal", description="Tüm mute ve banların loglanacağı kanalı belirler")
@app_commands.describe(kanal="Log kanalı")
@app_commands.guild_only()
@app_commands.default_permissions(manage_guild=True)
@app_commands.checks.has_permissions(manage_guild=True)
async def cezakanal(interaction: discord.Interaction, kanal: discord.TextChannel):
    await log_kanali_ayarla(interaction, "cezakanal", kanal, "Tüm mute/ban logları")


@cezakanal.error
async def cezakanal_hata(interaction: discord.Interaction, error: app_commands.AppCommandError):
    await izin_hatasi(interaction, error, "Sunucuyu Yönet")


# 13) /ping ve /bilgi
@bot.tree.command(name="ping", description="Botun gecikmesini gösterir")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message(f"Pong! {round(bot.latency * 1000)} ms")


@bot.tree.command(name="bilgi", description="Sunucu bilgisi")
async def bilgi(interaction: discord.Interaction):
    g = interaction.guild
    await interaction.response.send_message(f"**{g.name}** — {g.member_count} üye")


bot.run(TOKEN)
