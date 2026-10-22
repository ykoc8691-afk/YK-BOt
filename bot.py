# ---------- Roblox grup bağlantısı (/rutbe; Roblox adı RoWifi takma adından okunur) ----------
ROBLOX_API_KEY = os.getenv("ROBLOX_API_KEY")                 # Railway Variables'a eklenir, koda yazılmaz
ROBLOX_GROUP_ID = os.getenv("ROBLOX_GROUP_ID", "277876846")  # YK Studio grubu
_roblox_rol_onbellek = {"zaman": 0, "roller": []}
_roblox_rol_onbellek = {}   # grup_id -> {"zaman": ..., "roller": [...]}

# Branş toplulukları. discord_rol: kabul/rütbe verilince ana sunucuda otomatik verilecek rolün ADI (None = bot vermez, sen verirsin).
BRANSLAR = [
    {"kod": "AS.İZ", "ad": "Askeri İnzibat", "grup": "130878912", "discord_rol": None},
    {"kod": "ÖKK", "ad": "Özel Kuvvetler", "grup": "470332979", "discord_rol": None},
    {"kod": "HKK", "ad": "Hava Kuvvetleri", "grup": "147722829", "discord_rol": None},
    {"kod": "HM", "ad": "Hudut Müffetişleri", "grup": "1031355821", "discord_rol": None},
    {"kod": "JGK", "ad": "Jandarma", "grup": "669039361", "discord_rol": None},
    {"kod": "KK", "ad": "Kara Kuvvetleri", "grup": "637934685", "discord_rol": None},
]
BRANS_SECENEKLERI = [app_commands.Choice(name=f"{b['ad']} ({b['kod']})", value=b["kod"]) for b in BRANSLAR]


def brans_bul(kod: str):
    return next((b for b in BRANSLAR if b["kod"] == kod), None)


async def roblox_istek(yontem: str, url: str, **kw):
@@ -1565,13 +1580,15 @@ async def roblox_kullanici_bul(ad: str):
return (liste[0]["id"], liste[0]["name"]) if liste else (None, None)


async def roblox_roller(yenile: bool = False) -> list:
    """Gruptaki rütbeler: [{'id','displayName','rank','path'}, ...] (60 sn önbellekli)."""
    if not yenile and time.time() - _roblox_rol_onbellek["zaman"] < 60 and _roblox_rol_onbellek["roller"]:
        return _roblox_rol_onbellek["roller"]
async def roblox_roller(yenile: bool = False, grup_id: str = None) -> list:
    """Gruptaki rütbeler: [{'id','displayName','rank','path'}, ...] (60 sn önbellekli, grup başına)."""
    grup_id = str(grup_id or ROBLOX_GROUP_ID)
    ob = _roblox_rol_onbellek.get(grup_id)
    if not yenile and ob and time.time() - ob["zaman"] < 60 and ob["roller"]:
        return ob["roller"]
roller, sayfa = [], ""
for _ in range(5):
        url = f"https://apis.roblox.com/cloud/v2/groups/{ROBLOX_GROUP_ID}/roles?maxPageSize=100" + (f"&pageToken={sayfa}" if sayfa else "")
        url = f"https://apis.roblox.com/cloud/v2/groups/{grup_id}/roles?maxPageSize=100" + (f"&pageToken={sayfa}" if sayfa else "")
durum, veri = await roblox_istek("GET", url)
if durum != 200:
break
@@ -1580,15 +1597,35 @@ async def roblox_roller(yenile: bool = False) -> list:
if not sayfa:
break
if roller:
        _roblox_rol_onbellek.update(zaman=time.time(), roller=roller)
        _roblox_rol_onbellek[grup_id] = {"zaman": time.time(), "roller": roller}
return roller


async def roblox_uyelik_bul(kullanici_id: int):
async def roblox_katilma_istegi_bul(kullanici_id: int, grup_id: str):
    """Kullanıcının gruba gönderdiği bekleyen katılma isteği (yoksa None)."""
    sayfa = ""
    for _ in range(5):
        params = {"maxPageSize": 100}
        if sayfa:
            params["pageToken"] = sayfa
        durum, veri = await roblox_istek(
            "GET", f"https://apis.roblox.com/cloud/v2/groups/{grup_id}/join-requests", params=params)
        if durum != 200 or not isinstance(veri, dict):
            return None
        for r in veri.get("groupJoinRequests", []):
            if str(r.get("user", "")).endswith(f"/{kullanici_id}"):
                return r
        sayfa = veri.get("nextPageToken") or ""
        if not sayfa:
            break
    return None


async def roblox_uyelik_bul(kullanici_id: int, grup_id: str = None):
"""Kullanıcının gruptaki üyelik kaydı (yoksa None)."""
filtre = f"user == 'users/{kullanici_id}'"
durum, veri = await roblox_istek(
        "GET", f"https://apis.roblox.com/cloud/v2/groups/{ROBLOX_GROUP_ID}/memberships",
        "GET", f"https://apis.roblox.com/cloud/v2/groups/{grup_id or ROBLOX_GROUP_ID}/memberships",
params={"maxPageSize": 1, "filter": filtre})
liste = veri.get("groupMemberships") if isinstance(veri, dict) else None
return liste[0] if liste else None
@@ -1622,13 +1659,16 @@ def rutbe_yetkilisi_mi(interaction: discord.Interaction) -> bool:


@bot.tree.command(name="robloxrutbeler", description="Roblox grubundaki rütbeleri listeler (yönetici)")
@app_commands.describe(brans="Boş bırakırsan ana grup")
@app_commands.choices(brans=BRANS_SECENEKLERI)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def robloxrutbeler(interaction: discord.Interaction):
async def robloxrutbeler(interaction: discord.Interaction, brans: app_commands.Choice[str] = None):
if not rutbe_yetkilisi_mi(interaction):
return await interaction.response.send_message("Yetkin yok.", ephemeral=True)
await interaction.response.defer(ephemeral=True)
    roller = await roblox_roller(yenile=True)
    b = brans_bul(brans.value) if brans else None
    roller = await roblox_roller(yenile=True, grup_id=b["grup"] if b else None)
if not roller:
return await interaction.followup.send(
"Rütbeleri alamadım. `ROBLOX_API_KEY` ve `ROBLOX_GROUP_ID` doğru mu, anahtarda grup okuma izni var mı kontrol et.", ephemeral=True)
@@ -1645,6 +1685,100 @@ async def _rutbe_oto(interaction: discord.Interaction, current: str):
for r in roller if current.lower() in r.get("displayName", "").lower()][:25]


async def _brans_rutbe_oto(interaction: discord.Interaction, current: str):
    """Seçilen branşın Roblox rütbelerini önerir."""
    try:
        b = brans_bul(getattr(interaction.namespace, "brans", None))
        roller = await roblox_roller(grup_id=b["grup"]) if b else []
    except Exception:
        return []
    return [app_commands.Choice(name=r["displayName"][:100], value=r["displayName"][:100])
            for r in roller if current.lower() in r.get("displayName", "").lower()][:25]


async def _kisi_roblox(kisi: discord.Member):
    """Üyenin RoWifi takma adından Roblox hesabını bulur. ((id, ad), hata_metni)"""
    isim = nick_roblox_adi(kisi)
    rid, rad = await roblox_kullanici_bul(isim) if isim else (None, None)
    if not rid:
        return None, (f"{kisi.display_name} takma adından Roblox hesabını bulamadım (okunan ad: `{isim or '-'}`). "
                      f"Takma ad `[RÜTBE] RobloxAdı` ya da `RobloxAdı` biçiminde olmalı.")
    return {"id": rid, "ad": rad}, None


def _brans_discord_rolu(guild: discord.Guild, b: dict):
    return discord.utils.get(guild.roles, name=b["discord_rol"]) if b.get("discord_rol") else None


@bot.tree.command(name="kabul", description="Üyenin branş topluluğuna gönderdiği katılma isteğini kabul eder (yönetici)")
@app_commands.describe(kisi="Kabul edilecek üye", brans="Branş")
@app_commands.choices(brans=BRANS_SECENEKLERI)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def kabul(interaction: discord.Interaction, kisi: discord.Member, brans: app_commands.Choice[str]):
    if not rutbe_yetkilisi_mi(interaction):
        return await interaction.response.send_message("Bu komutu kullanma yetkin yok.", ephemeral=True)
    if not ROBLOX_API_KEY:
        return await interaction.response.send_message("`ROBLOX_API_KEY` tanımlı değil.", ephemeral=True)
    b = brans_bul(brans.value)
    await interaction.response.defer(ephemeral=True)
    bag, hata = await _kisi_roblox(kisi)
    if hata:
        return await interaction.followup.send(hata, ephemeral=True)
    if await roblox_uyelik_bul(bag["id"], b["grup"]):
        return await interaction.followup.send(f"**{bag['ad']}** zaten **{b['ad']}** grubunda.", ephemeral=True)
    istek = await roblox_katilma_istegi_bul(bag["id"], b["grup"])
    if not istek:
        return await interaction.followup.send(
            f"**{bag['ad']}** için **{b['ad']}** grubunda bekleyen katılma isteği bulamadım. "
            f"Önce Roblox'ta gruba katılma isteği göndermeli (ya da botun isteği görme/kabul etme yetkisi yok).", ephemeral=True)
    durum, veri = await roblox_istek("POST", f"https://apis.roblox.com/cloud/v2/{istek['path']}:accept", json={})
    if durum != 200:
        return await interaction.followup.send(
            f"❌ Katılma isteği kabul edilemedi (hata {durum}). Anahtarın bu gruba izni ve hesabın yetkisi var mı kontrol et.", ephemeral=True)
    satirlar = [f"✅ **{bag['ad']}**, **{b['ad']}** grubuna kabul edildi."]
    rol = _brans_discord_rolu(interaction.guild, b)
    if rol:
        try:
            await kisi.add_roles(rol, reason=f"/kabul: {interaction.user}")
            satirlar.append(f"✅ Discord: {rol.mention} verildi")
        except discord.Forbidden:
            satirlar.append("❌ Discord rolü verilemedi (bot rolü bu rolün üstünde olmalı).")
    await interaction.followup.send("\n".join(satirlar), ephemeral=True)
    await rutbe_logla(interaction.guild, f"✅ {interaction.user.mention} → {kisi.mention} (Roblox: {bag['ad']}) "
                                         f"**{b['ad']}** grubuna kabul edildi" + (f" / {rol.mention}" if rol else ""))


@bot.tree.command(name="bransrutbe", description="Üyeye branş topluluğunda rütbe verir (yönetici)")
@app_commands.describe(kisi="Rütbe verilecek üye", brans="Branş", roblox_rutbe="Branş grubundaki rütbe adı")
@app_commands.choices(brans=BRANS_SECENEKLERI)
@app_commands.autocomplete(roblox_rutbe=_brans_rutbe_oto)
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
async def bransrutbe(interaction: discord.Interaction, kisi: discord.Member, brans: app_commands.Choice[str], roblox_rutbe: str):
    if not rutbe_yetkilisi_mi(interaction):
        return await interaction.response.send_message("Bu komutu kullanma yetkin yok.", ephemeral=True)
    if not ROBLOX_API_KEY:
        return await interaction.response.send_message("`ROBLOX_API_KEY` tanımlı değil.", ephemeral=True)
    b = brans_bul(brans.value)
    await interaction.response.defer(ephemeral=True)
    roller = await roblox_roller(yenile=True, grup_id=b["grup"])
    if not roller:
        return await interaction.followup.send(
            f"**{b['ad']}** grubunun rütbelerini alamadım. Anahtarın bu gruba izni var mı kontrol et.", ephemeral=True)
    hedef = next((r for r in roller if _ad_norm(r.get("displayName", "")) == _ad_norm(roblox_rutbe)), None)
    if not hedef:
        yakin = difflib.get_close_matches(roblox_rutbe, [r["displayName"] for r in roller], n=3, cutoff=0.5)
        ipucu = f" Şunu mu demek istedin: {', '.join(yakin)}?" if yakin else ""
        return await interaction.followup.send(f"**{b['ad']}** grubunda **{roblox_rutbe}** adında rütbe yok.{ipucu}", ephemeral=True)
    bag, hata = await _kisi_roblox(kisi)
    if hata:
        return await interaction.followup.send(hata, ephemeral=True)
    satirlar = await rutbe_isle(interaction.guild, kisi, hedef, _brans_discord_rolu(interaction.guild, b),
                                interaction.user, bag, grup_id=b["grup"], grup_adi=b["ad"])
    await interaction.followup.send("\n".join(satirlar)[:1900], ephemeral=True)


@bot.tree.command(name="rutbelogkanal", description="Rütbe verme işlemlerinin loglanacağı kanalı seçer (yönetici)")
@app_commands.default_permissions(administrator=True)
@app_commands.guild_only()
@@ -1684,34 +1818,48 @@ def discord_rol_bul(guild: discord.Guild, ad: str):
return (harita[yakin[0]], False) if yakin else (None, False)


async def rutbe_isle(guild: discord.Guild, kisi: discord.Member, hedef: dict, discord_rol: discord.Role,
                     yapan: discord.abc.User, bag: dict) -> list:
async def rutbe_logla(guild: discord.Guild, metin: str):
    """/rutbelogkanal ile seçilen kanala log yazar (seçilmemişse sessizce geçer). Kimseyi etiketleyip bildirim göndermez."""
    kid = json_yukle(AYAR_DOSYASI).get(str(guild.id), {}).get("rutbe_log_kanal")
    kanal = guild.get_channel(kid) if kid else None
    if kanal:
        try:
            await kanal.send(metin[:1900], allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException:
            pass


async def rutbe_isle(guild: discord.Guild, kisi: discord.Member, hedef: dict, discord_rol,
                     yapan: discord.abc.User, bag: dict, grup_id: str = None, grup_adi: str = "") -> list:
"""Önce Roblox rütbesini verir; sadece o başarılıysa Discord rolünü verir. Sonuç satırlarını döner."""
if hedef.get("rank", 0) >= 255 or hedef.get("rank", 0) == 0:
return ["❌ Grup sahibi ve ziyaretçi (0) rütbeleri bot ile verilmez."]
    uyelik = await roblox_uyelik_bul(bag["id"])
    grup_id = str(grup_id or ROBLOX_GROUP_ID)
    uyelik = await roblox_uyelik_bul(bag["id"], grup_id)
if not uyelik:
return [f"❌ **{bag['ad']}** Roblox grubunda değil. Önce gruba katılmalı."]
    roller = await roblox_roller()
    roller = await roblox_roller(grup_id=grup_id)
mevcut = next((r for r in roller if r.get("path") == uyelik.get("role")), None)
if mevcut and mevcut.get("rank", 0) >= 255:
return ["❌ Grup sahibinin rütbesi değiştirilemez."]
durum, veri = await roblox_istek("PATCH", f"https://apis.roblox.com/cloud/v2/{uyelik['path']}",
                                     json={"role": f"groups/{ROBLOX_GROUP_ID}/roles/{_rol_id(hedef)}"})
                                     json={"role": f"groups/{grup_id}/roles/{_rol_id(hedef)}"})
if durum != 200:
return [f"❌ Roblox rütbesi değişmedi (hata {durum}). Anahtarın izinlerini ve bot hesabının rütbesini kontrol et; "
f"bot hesabı verilecek rütbenin üstünde olmalı."]
    satirlar = [f"✅ Roblox: **{bag['ad']}** → **{hedef['displayName']}**"]
    try:
        await kisi.add_roles(discord_rol, reason=f"/rutbe: {yapan}")
        satirlar.append(f"✅ Discord: {discord_rol.mention} verildi")
    except discord.Forbidden:
        satirlar.append("❌ Discord rolü verilemedi (bot rolü bu rolün üstünde olmalı). Roblox rütbesi verildi, rolü elle ver.")
    satirlar = [f"✅ Roblox{(' (' + grup_adi + ')') if grup_adi else ''}: **{bag['ad']}** → **{hedef['displayName']}**"]
    if discord_rol:
        try:
            await kisi.add_roles(discord_rol, reason=f"/rutbe: {yapan}")
            satirlar.append(f"✅ Discord: {discord_rol.mention} verildi")
        except discord.Forbidden:
            satirlar.append("❌ Discord rolü verilemedi (bot rolü bu rolün üstünde olmalı). Roblox rütbesi verildi, rolü elle ver.")
kid = json_yukle(AYAR_DOSYASI).get(str(guild.id), {}).get("rutbe_log_kanal")
kanal = guild.get_channel(kid) if kid else None
if kanal:
try:
            await kanal.send(f"🎖️ {yapan.mention} → {kisi.mention} (Roblox: {bag['ad']}): **{hedef['displayName']}** / {discord_rol.mention}",
            await kanal.send(f"🎖️ {yapan.mention} → {kisi.mention} (Roblox: {bag['ad']}){(' [' + grup_adi + ']') if grup_adi else ''}: "
                             f"**{hedef['displayName']}**" + (f" / {discord_rol.mention}" if discord_rol else ""),
allowed_mentions=discord.AllowedMentions.none())
except discord.HTTPException:
pass
