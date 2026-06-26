"""
Bot interativo do Telegram para busca de processos do TJRJ
Hospedado no Render como webhook FastAPI
"""
from fastapi import FastAPI, Request
import requests
import json
import os
from google import genai

app = FastAPI()

# Configurações via variáveis de ambiente (configuradas no Render)
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
DATAJUD_API_KEY = os.environ["DATAJUD_API_KEY"]
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]

ENDPOINT_DATAJUD = "https://api-publica.datajud.cnj.jus.br/api_publica_tjrj/_search"
HEADERS_DATAJUD = {
    "Authorization": f"APIKey {DATAJUD_API_KEY}",
    "Content-Type": "application/json"
}
TELEGRAM_URL = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}"


# ── Funções auxiliares ─────────────────────────────────────────────────────────

def enviar_mensagem(chat_id, texto, parse_mode="HTML"):
    requests.post(f"{TELEGRAM_URL}/sendMessage", json={
        "chat_id": chat_id,
        "text": texto,
        "parse_mode": parse_mode
    })


def buscar_processos(termo, tamanho=10):
    query = {
        "size": tamanho,
        "sort": [{"dataHoraUltimaAtualizacao": {"order": "desc"}}],
        "query": {
            "match": {
                "assuntos.nome": {
                    "query": termo,
                    "operator": "and",
                    "fuzziness": "AUTO"
                }
            }
        }
    }
    resp = requests.post(
        ENDPOINT_DATAJUD,
        json=query,
        headers=HEADERS_DATAJUD,
        timeout=30
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", {})
    total = hits.get("total", {}).get("value", 0)
    processos = []
    for hit in hits.get("hits", []):
        f = hit["_source"]
        movs = sorted(f.get("movimentos", []), key=lambda m: m.get("dataHora", ""))
        processos.append({
            "numeroProcesso": f.get("numeroProcesso"),
            "classe": f.get("classe", {}).get("nome"),
            "orgaoJulgador": f.get("orgaoJulgador", {}).get("nome"),
            "assuntos": [a.get("nome") for a in f.get("assuntos", [])],
            "ultimoMovimento": movs[-1].get("nome") if movs else "N/A",
            "dataUltimoMovimento": movs[-1].get("dataHora", "")[:10] if movs else "N/A",
        })
    return processos, total


def traduzir_processo(processo):
    client = genai.Client(api_key=GEMINI_API_KEY)
    prompt = f"""Você é o "JurisTradutor STF". Traduza este processo do TJRJ para linguagem simples.

Processo:
{json.dumps(processo, ensure_ascii=False, indent=2)}

Retorne APENAS um objeto JSON com:
- "titulo": título em linguagem simples
- "resumo_simples": 2-3 frases sem juridiquês
- "por_que_importa": impacto para o cidadão (1-2 frases)

Sem markdown, sem texto extra, só o JSON."""

    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )
    texto = response.text.strip().replace("```json", "").replace("```", "").strip()
    return json.loads(texto)


def formatar_resultados(processos, termo, total):
    if not processos:
        return f"🔍 <b>Busca: {termo}</b>\n\nNenhum processo encontrado."

    linhas = [f"🔍 <b>Busca: {termo}</b>\n{len(processos)} de {total} resultado(s)\n"]
    for i, p in enumerate(processos, 1):
        assuntos = ", ".join(p["assuntos"][:2]) or "—"
        linhas.append(
            f"<b>{i}. {p['classe'] or 'Processo'}</b>\n"
            f"📁 <code>{p['numeroProcesso']}</code>\n"
            f"📌 {assuntos}\n"
            f"🔄 {p['ultimoMovimento']} ({p['dataUltimoMovimento']})\n"
        )
    linhas.append("\n💡 Para traduzir um processo, envie:\n<code>/traduzir NUMERO_DO_PROCESSO</code>")
    return "\n".join(linhas)


# ── Handlers de comandos ───────────────────────────────────────────────────────

def handle_start(chat_id):
    enviar_mensagem(chat_id,
        "⚖️ <b>Monitor TJRJ — Bot de Busca</b>\n\n"
        "Comandos disponíveis:\n\n"
        "🔍 <code>/buscar [assunto]</code>\n"
        "Busca processos por assunto no TJRJ\n"
        "Ex: <code>/buscar superendividamento</code>\n\n"
        "✨ <code>/traduzir [número]</code>\n"
        "Traduz um processo específico com IA\n"
        "Ex: <code>/traduzir 00180298820268190000</code>"
    )


def handle_buscar(chat_id, termo):
    if not termo.strip():
        enviar_mensagem(chat_id, "⚠️ Use: <code>/buscar [assunto]</code>\nEx: <code>/buscar acidente</code>")
        return

    enviar_mensagem(chat_id, f"🔍 Buscando processos sobre <b>{termo}</b>...")

    try:
        processos, total = buscar_processos(termo.strip(), tamanho=10)
        texto = formatar_resultados(processos, termo, total)
        enviar_mensagem(chat_id, texto)
    except requests.exceptions.ConnectTimeout:
        enviar_mensagem(chat_id, "⏱️ Timeout ao conectar com o DataJud. Tente novamente.")
    except Exception as e:
        enviar_mensagem(chat_id, f"❌ Erro na busca: {type(e).__name__}")


def handle_traduzir(chat_id, numero):
    if not numero.strip():
        enviar_mensagem(chat_id, "⚠️ Use: <code>/traduzir [número]</code>\nEx: <code>/traduzir 00180298820268190000</code>")
        return

    enviar_mensagem(chat_id, f"✨ Traduzindo processo <code>{numero}</code>...")

    try:
        # Busca o processo pelo número exato
        query = {
            "size": 1,
            "query": {"match": {"numeroProcesso": numero.strip()}}
        }
        resp = requests.post(ENDPOINT_DATAJUD, json=query, headers=HEADERS_DATAJUD, timeout=30)
        resp.raise_for_status()
        hits = resp.json().get("hits", {}).get("hits", [])

        if not hits:
            enviar_mensagem(chat_id, f"❌ Processo <code>{numero}</code> não encontrado.")
            return

        f = hits[0]["_source"]
        movs = sorted(f.get("movimentos", []), key=lambda m: m.get("dataHora", ""))
        processo = {
            "numeroProcesso": f.get("numeroProcesso"),
            "classe": f.get("classe", {}).get("nome"),
            "orgaoJulgador": f.get("orgaoJulgador", {}).get("nome"),
            "assuntos": [a.get("nome") for a in f.get("assuntos", [])],
            "ultimoMovimento": movs[-1].get("nome") if movs else "N/A",
        }

        trad = traduzir_processo(processo)
        texto = (
            f"⚖️ <b>{trad.get('titulo', '')}</b>\n\n"
            f"📁 <code>{numero}</code>\n\n"
            f"📝 {trad.get('resumo_simples', '—')}\n\n"
            f"💡 <i>{trad.get('por_que_importa', '—')}</i>"
        )
        enviar_mensagem(chat_id, texto)

    except Exception as e:
        enviar_mensagem(chat_id, f"❌ Erro ao traduzir: {type(e).__name__}")


# ── Webhook endpoint ───────────────────────────────────────────────────────────

@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()

    message = data.get("message", {})
    chat_id = message.get("chat", {}).get("id")
    texto = message.get("text", "").strip()

    if not chat_id or not texto:
        return {"ok": True}

    if texto.startswith("/start"):
        handle_start(chat_id)
    elif texto.startswith("/buscar"):
        termo = texto.replace("/buscar", "").strip()
        handle_buscar(chat_id, termo)
    elif texto.startswith("/traduzir"):
        numero = texto.replace("/traduzir", "").strip()
        handle_traduzir(chat_id, numero)
    else:
        enviar_mensagem(chat_id,
            "❓ Comando não reconhecido. Use:\n"
            "• <code>/buscar [assunto]</code>\n"
            "• <code>/traduzir [número]</code>\n"
            "• <code>/start</code> para ver os comandos"
        )

    return {"ok": True}


@app.get("/")
def root():
    return {"status": "Monitor TJRJ Bot rodando"}
