from flask import Flask, render_template, request, redirect, url_for, jsonify, flash, send_file
import json
import os
import secrets
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or secrets.token_hex(32)
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = Path(os.environ.get("DB_PATH", str(BASE_DIR / "mercado.db")))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

BACKUP_DIR = Path(os.environ.get("BACKUP_DIR", str(BASE_DIR / "backups")))
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

COSMOS_API_TOKEN = os.environ.get("COSMOS_API_TOKEN", "").strip()
COSMOS_USER_AGENT = os.environ.get("COSMOS_USER_AGENT", "").strip()

UNIDADES = [
    ("un", "Unidade (un)"),
    ("kg", "Quilograma (kg)"),
    ("g", "Grama (g)"),
    ("L", "Litro (L)"),
    ("ml", "Mililitro (ml)"),
    ("pct", "Pacote (pct)"),
    ("cx", "Caixa (cx)"),
    ("dz", "Dúzia (dz)"),
]
UNIDADES_VALIDAS = {u for u, _ in UNIDADES}


def calc_item_total(quantidade, unidade, valor_unitario):
    q = Decimal(str(quantidade or 0))
    price = Decimal(str(valor_unitario or 0))
    unit = str(unidade or "un").lower()
    if unit in ("g", "ml"):
        q = q / Decimal("1000")
    return float((q * price).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.create_function("item_total", 3, calc_item_total)
    return conn


def parse_money(value):
    if value is None:
        return 0.0
    s = str(value).strip().replace("R$", "").replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    return float(s or 0)


def parse_number(value, default=0.0):
    try:
        return float(str(value).strip().replace(",", "."))
    except (TypeError, ValueError):
        return default


def money_br(value):
    value = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"R$ {value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def quantity_br(value):
    value = float(value or 0)
    if value.is_integer():
        return str(int(value))
    return f"{value:.3f}".rstrip("0").rstrip(".").replace(".", ",")


def price_suffix(unidade):
    if unidade in ("kg", "g"):
        return "/kg"
    if unidade in ("L", "ml"):
        return "/L"
    if unidade:
        return f"/{unidade}"
    return "/un"


def item_total_expr(alias="i"):
    return f"item_total({alias}.quantidade, {alias}.unidade, {alias}.valor_unitario)"



app.jinja_env.filters["brl"] = money_br
app.jinja_env.filters["qtd"] = quantity_br
app.jinja_env.globals["price_suffix"] = price_suffix


def has_column(conn, table, column):
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row["name"] == column for row in rows)


def init_db():
    conn = get_db()
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS categorias (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS mercados (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS produtos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL UNIQUE COLLATE NOCASE,
        categoria_id INTEGER,
        codigo_barras TEXT,
        unidade TEXT NOT NULL DEFAULT 'un',
        ativo INTEGER NOT NULL DEFAULT 1,
        FOREIGN KEY (categoria_id) REFERENCES categorias(id)
    );

    CREATE TABLE IF NOT EXISTS compras (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        mercado_id INTEGER,
        data TEXT NOT NULL,
        orcamento REAL NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'aberta',
        FOREIGN KEY (mercado_id) REFERENCES mercados(id)
    );

    CREATE TABLE IF NOT EXISTS itens_compra (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        compra_id INTEGER NOT NULL,
        produto_id INTEGER NOT NULL,
        quantidade REAL NOT NULL DEFAULT 1,
        unidade TEXT NOT NULL DEFAULT 'un',
        valor_unitario REAL NOT NULL DEFAULT 0,
        FOREIGN KEY (compra_id) REFERENCES compras(id) ON DELETE CASCADE,
        FOREIGN KEY (produto_id) REFERENCES produtos(id)
    );

    CREATE TABLE IF NOT EXISTS configuracoes_app (
        chave TEXT PRIMARY KEY,
        valor TEXT NOT NULL DEFAULT ''
    );

    CREATE TABLE IF NOT EXISTS listas (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nome TEXT NOT NULL,
        data TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS itens_lista (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        lista_id INTEGER NOT NULL,
        produto_id INTEGER NOT NULL,
        quantidade REAL NOT NULL DEFAULT 1,
        comprado INTEGER NOT NULL DEFAULT 0,
        FOREIGN KEY (lista_id) REFERENCES listas(id) ON DELETE CASCADE,
        FOREIGN KEY (produto_id) REFERENCES produtos(id)
    );
    """)

    # Migrações seguras para bancos criados nas versões anteriores.
    if not has_column(conn, "produtos", "unidade"):
        conn.execute("ALTER TABLE produtos ADD COLUMN unidade TEXT NOT NULL DEFAULT 'un'")
    if not has_column(conn, "itens_compra", "unidade"):
        conn.execute("ALTER TABLE itens_compra ADD COLUMN unidade TEXT NOT NULL DEFAULT 'un'")

    defaults = ["Alimentos", "Bebidas", "Higiene", "Limpeza", "Açougue", "Hortifruti", "Outros"]
    for nome in defaults:
        conn.execute("INSERT OR IGNORE INTO categorias(nome) VALUES (?)", (nome,))

    conn.execute("CREATE INDEX IF NOT EXISTS idx_produtos_codigo ON produtos(codigo_barras)")
    conn.commit()
    conn.close()



def catalogo_produtos_payload():
    """Monta um backup portátil do catálogo, sem compras nem credenciais."""
    conn = get_db()
    categorias = [
        row["nome"]
        for row in conn.execute("SELECT nome FROM categorias ORDER BY nome").fetchall()
    ]
    produtos = [
        dict(row)
        for row in conn.execute("""
            SELECT
                p.nome,
                p.codigo_barras,
                COALESCE(p.unidade, 'un') AS unidade,
                c.nome AS categoria
            FROM produtos p
            LEFT JOIN categorias c ON c.id = p.categoria_id
            WHERE p.ativo = 1
            ORDER BY p.nome
        """).fetchall()
    ]
    conn.close()

    return {
        "app": "Meu Mercado",
        "tipo": "catalogo_produtos",
        "versao": 1,
        "criado_em": datetime.now().isoformat(timespec="seconds"),
        "categorias": categorias,
        "produtos": produtos,
    }


def gravar_backup_produtos(nome_arquivo="produtos_automatico.json"):
    """Grava um JSON de forma atômica dentro da pasta persistente de backups."""
    payload = catalogo_produtos_payload()
    destino = BACKUP_DIR / nome_arquivo
    temporario = BACKUP_DIR / (nome_arquivo + ".tmp")

    with temporario.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

    temporario.replace(destino)
    return destino, len(payload["produtos"])


def atualizar_backup_automatico():
    """Atualiza o backup sem impedir o funcionamento do app em caso de erro de disco."""
    try:
        gravar_backup_produtos()
    except (OSError, sqlite3.Error, ValueError):
        app.logger.exception("Não foi possível atualizar o backup automático de produtos.")


def info_backup_produtos():
    conn = get_db()
    total = conn.execute(
        "SELECT COUNT(*) AS total FROM produtos WHERE ativo=1"
    ).fetchone()["total"]
    conn.close()

    arquivo = BACKUP_DIR / "produtos_automatico.json"
    if arquivo.exists():
        atualizado_em = datetime.fromtimestamp(arquivo.stat().st_mtime).strftime("%d/%m/%Y %H:%M")
    else:
        atualizado_em = None

    return {
        "total_produtos": total,
        "automatico_existe": arquivo.exists(),
        "atualizado_em": atualizado_em,
    }


def restaurar_catalogo_produtos(payload):
    if not isinstance(payload, dict):
        raise ValueError("Arquivo de backup inválido.")
    if payload.get("tipo") != "catalogo_produtos":
        raise ValueError("Este arquivo não é um backup de produtos do Meu Mercado.")

    produtos = payload.get("produtos")
    categorias = payload.get("categorias", [])
    if not isinstance(produtos, list) or not isinstance(categorias, list):
        raise ValueError("Estrutura de backup inválida.")
    if len(produtos) > 50000:
        raise ValueError("O arquivo possui produtos demais para uma restauração.")

    conn = get_db()
    restaurados = 0
    ignorados = 0

    try:
        conn.execute("BEGIN")

        for nome_categoria in categorias:
            nome_categoria = str(nome_categoria or "").strip()
            if nome_categoria:
                conn.execute(
                    "INSERT OR IGNORE INTO categorias(nome) VALUES (?)",
                    (nome_categoria,),
                )

        for item in produtos:
            if not isinstance(item, dict):
                ignorados += 1
                continue

            nome = str(item.get("nome") or "").strip()
            codigo = str(item.get("codigo_barras") or "").strip() or None
            unidade = str(item.get("unidade") or "un").strip()
            categoria_nome = str(item.get("categoria") or "").strip()

            if not nome:
                ignorados += 1
                continue
            if unidade not in UNIDADES_VALIDAS:
                unidade = "un"

            categoria_id = None
            if categoria_nome:
                conn.execute(
                    "INSERT OR IGNORE INTO categorias(nome) VALUES (?)",
                    (categoria_nome,),
                )
                row_cat = conn.execute(
                    "SELECT id FROM categorias WHERE nome=? COLLATE NOCASE",
                    (categoria_nome,),
                ).fetchone()
                if row_cat:
                    categoria_id = row_cat["id"]

            existente = None
            if codigo:
                existente = conn.execute(
                    "SELECT id FROM produtos WHERE codigo_barras=? LIMIT 1",
                    (codigo,),
                ).fetchone()

            if not existente:
                existente = conn.execute(
                    "SELECT id FROM produtos WHERE nome=? COLLATE NOCASE LIMIT 1",
                    (nome,),
                ).fetchone()

            if existente:
                # O nome pode conflitar com outro cadastro; nesse caso preservamos o
                # nome atual e ainda restauramos código, categoria e unidade.
                try:
                    conn.execute("""
                        UPDATE produtos
                        SET nome=?, codigo_barras=COALESCE(?, codigo_barras),
                            categoria_id=?, unidade=?, ativo=1
                        WHERE id=?
                    """, (nome, codigo, categoria_id, unidade, existente["id"]))
                except sqlite3.IntegrityError:
                    conn.execute("""
                        UPDATE produtos
                        SET codigo_barras=COALESCE(?, codigo_barras),
                            categoria_id=?, unidade=?, ativo=1
                        WHERE id=?
                    """, (codigo, categoria_id, unidade, existente["id"]))
            else:
                try:
                    conn.execute("""
                        INSERT INTO produtos(nome, categoria_id, codigo_barras, unidade, ativo)
                        VALUES (?, ?, ?, ?, 1)
                    """, (nome, categoria_id, codigo, unidade))
                except sqlite3.IntegrityError:
                    ignorados += 1
                    continue

            restaurados += 1

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    atualizar_backup_automatico()
    return restaurados, ignorados

def active_purchase(conn):
    return conn.execute("""
        SELECT c.*, m.nome AS mercado
        FROM compras c
        LEFT JOIN mercados m ON m.id = c.mercado_id
        WHERE c.status='aberta'
        ORDER BY c.id DESC
        LIMIT 1
    """).fetchone()


def purchase_total(conn, compra_id):
    expr = item_total_expr("i")
    row = conn.execute(f"""
        SELECT COALESCE(SUM({expr}), 0) AS total
        FROM itens_compra i
        WHERE i.compra_id=?
    """, (compra_id,)).fetchone()
    return float(row["total"] or 0)


def produto_payload(conn, produto):
    if not produto:
        return None
    preco = conn.execute("""
        SELECT valor_unitario, unidade
        FROM itens_compra
        WHERE produto_id=?
        ORDER BY id DESC LIMIT 1
    """, (produto["id"],)).fetchone()
    menor = conn.execute("""
        SELECT MIN(valor_unitario) AS menor_preco
        FROM itens_compra
        WHERE produto_id=?
    """, (produto["id"],)).fetchone()
    return {
        "id": produto["id"],
        "nome": produto["nome"],
        "categoria_id": produto["categoria_id"],
        "codigo_barras": produto["codigo_barras"],
        "unidade": produto["unidade"] or "un",
        "ultimo_preco": preco["valor_unitario"] if preco else None,
        "menor_preco": menor["menor_preco"] if menor else None,
        "fonte": "local",
    }


def buscar_open_food_facts(codigo):
    """Consulta o Open Food Facts.

    Retorna (produto, status), onde status pode ser:
    encontrado, nao_encontrado, codigo_invalido ou indisponivel.
    """
    if not codigo.isdigit() or not 8 <= len(codigo) <= 14:
        return None, "codigo_invalido"

    fields = "product_name,product_name_pt,brands"
    url = (
        "https://world.openfoodfacts.org/api/v2/product/"
        + urllib.parse.quote(codigo)
        + "?fields="
        + urllib.parse.quote(fields)
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "MeuMercado/1.0 (aplicativo pessoal de compras)"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "nao_encontrado"
        return None, "indisponivel"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None, "indisponivel"

    if data.get("status") != 1:
        return None, "nao_encontrado"

    product = data.get("product") or {}
    nome = (product.get("product_name_pt") or product.get("product_name") or "").strip()
    marca = (product.get("brands") or "").split(",")[0].strip()
    if not nome:
        return None, "nao_encontrado"
    if marca and marca.lower() not in nome.lower():
        nome = f"{nome} - {marca}"

    return {"nome": nome, "fonte": "openfoodfacts"}, "encontrado"


def cosmos_credentials():
    """Lê credenciais do ambiente ou, na ausência delas, do banco local."""
    if COSMOS_API_TOKEN and COSMOS_USER_AGENT:
        return COSMOS_API_TOKEN, COSMOS_USER_AGENT, "ambiente"

    conn = get_db()
    rows = conn.execute(
        "SELECT chave, valor FROM configuracoes_app WHERE chave IN ('cosmos_api_token', 'cosmos_user_agent')"
    ).fetchall()
    conn.close()
    cfg = {row["chave"]: row["valor"] for row in rows}
    token = (cfg.get("cosmos_api_token") or "").strip()
    user_agent = (cfg.get("cosmos_user_agent") or "").strip()
    return token, user_agent, "app"


def cosmos_status():
    token, user_agent, origem = cosmos_credentials()
    return bool(token and user_agent), user_agent, origem


def buscar_cosmos(codigo):
    """Consulta o Bluesoft Cosmos quando token e User-Agent estão configurados.

    Retorna (produto, status), onde status pode ser:
    encontrado, nao_configurado, nao_encontrado, limite, autenticacao ou indisponivel.
    """
    token, user_agent, _ = cosmos_credentials()
    if not token or not user_agent:
        return None, "nao_configurado"
    if not codigo.isdigit() or not 8 <= len(codigo) <= 14:
        return None, "nao_encontrado"

    url = (
        "https://cosmos.bluesoft.com.br/api/gtins/"
        + urllib.parse.quote(codigo)
        + ".json"
    )
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": user_agent,
            "Content-Type": "application/json",
            "X-Cosmos-Token": token,
        },
    )

    try:
        with urllib.request.urlopen(req, timeout=6) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None, "nao_encontrado"
        if exc.code == 429:
            return None, "limite"
        if exc.code in (401, 403):
            return None, "autenticacao"
        return None, "indisponivel"
    except (urllib.error.URLError, TimeoutError, ValueError, OSError):
        return None, "indisponivel"

    nome = str(data.get("description") or "").strip()
    marca_obj = data.get("brand") or {}
    marca = str(marca_obj.get("name") or "").strip() if isinstance(marca_obj, dict) else ""
    if not nome:
        return None, "nao_encontrado"
    if marca and marca.lower() not in nome.lower():
        nome = f"{nome} - {marca}"

    return {
        "nome": nome,
        "fonte": "cosmos",
        "imagem": data.get("thumbnail"),
        "peso_liquido": data.get("net_weight"),
    }, "encontrado"


init_db()

# Cria a primeira cópia automática quando o catálogo já existir.
if not (BACKUP_DIR / "produtos_automatico.json").exists():
    atualizar_backup_automatico()


@app.route("/")
def index():
    conn = get_db()
    compra = active_purchase(conn)
    categorias = conn.execute("SELECT * FROM categorias ORDER BY nome").fetchall()
    mercados = conn.execute("SELECT * FROM mercados ORDER BY nome").fetchall()

    itens = []
    total = 0
    if compra:
        expr = item_total_expr("i")
        itens = conn.execute(f"""
            SELECT i.*, p.nome AS produto, p.codigo_barras, cat.nome AS categoria,
                   {expr} AS subtotal
            FROM itens_compra i
            JOIN produtos p ON p.id = i.produto_id
            LEFT JOIN categorias cat ON cat.id = p.categoria_id
            WHERE i.compra_id=?
            ORDER BY i.id DESC
        """, (compra["id"],)).fetchall()
        total = purchase_total(conn, compra["id"])

    conn.close()
    return render_template(
        "index.html",
        compra=compra,
        itens=itens,
        total=total,
        categorias=categorias,
        mercados=mercados,
        unidades=UNIDADES,
    )


@app.post("/compra/iniciar")
def iniciar_compra():
    mercado_nome = request.form.get("mercado", "").strip()
    orcamento = parse_money(request.form.get("orcamento"))

    conn = get_db()
    atual = active_purchase(conn)
    if atual:
        conn.close()
        flash("Já existe uma compra em andamento.")
        return redirect(url_for("index"))

    mercado_id = None
    if mercado_nome:
        conn.execute("INSERT OR IGNORE INTO mercados(nome) VALUES (?)", (mercado_nome,))
        mercado = conn.execute("SELECT id FROM mercados WHERE nome=? COLLATE NOCASE", (mercado_nome,)).fetchone()
        mercado_id = mercado["id"]

    conn.execute(
        "INSERT INTO compras(mercado_id, data, orcamento, status) VALUES (?, ?, ?, 'aberta')",
        (mercado_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), orcamento)
    )
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.post("/compra/<int:compra_id>/item")
def adicionar_item(compra_id):
    nome = request.form.get("produto", "").strip()
    codigo = request.form.get("codigo_barras", "").strip() or None
    categoria_id = request.form.get("categoria_id") or None
    quantidade = parse_number(request.form.get("quantidade"), 1)
    unidade = request.form.get("unidade", "un").strip()
    if unidade not in UNIDADES_VALIDAS:
        unidade = "un"
    valor = parse_money(request.form.get("valor"))

    if not nome or quantidade <= 0 or valor < 0:
        flash("Preencha produto, quantidade e valor corretamente.")
        return redirect(url_for("index"))

    conn = get_db()
    produto = None
    if codigo:
        produto = conn.execute(
            "SELECT * FROM produtos WHERE codigo_barras=? AND ativo=1 LIMIT 1", (codigo,)
        ).fetchone()
    if not produto:
        produto = conn.execute("SELECT * FROM produtos WHERE nome=? COLLATE NOCASE", (nome,)).fetchone()

    if produto:
        produto_id = produto["id"]
        conn.execute(
            "UPDATE produtos SET categoria_id=COALESCE(?, categoria_id), codigo_barras=COALESCE(?, codigo_barras), unidade=?, ativo=1 WHERE id=?",
            (categoria_id, codigo, unidade, produto_id),
        )
    else:
        cur = conn.execute(
            "INSERT INTO produtos(nome, categoria_id, codigo_barras, unidade) VALUES (?, ?, ?, ?)",
            (nome, categoria_id, codigo, unidade),
        )
        produto_id = cur.lastrowid

    conn.execute("""
        INSERT INTO itens_compra(compra_id, produto_id, quantidade, unidade, valor_unitario)
        VALUES (?, ?, ?, ?, ?)
    """, (compra_id, produto_id, quantidade, unidade, valor))
    conn.commit()
    conn.close()
    atualizar_backup_automatico()
    return redirect(url_for("index"))


@app.post("/item/<int:item_id>/excluir")
def excluir_item(item_id):
    conn = get_db()
    conn.execute("DELETE FROM itens_compra WHERE id=?", (item_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("index"))


@app.post("/item/<int:item_id>/editar")
def editar_item(item_id):
    quantidade = parse_number(request.form.get("quantidade"), 1)
    unidade = request.form.get("unidade", "un").strip()
    if unidade not in UNIDADES_VALIDAS:
        unidade = "un"
    valor = parse_money(request.form.get("valor"))
    if quantidade <= 0 or valor < 0:
        flash("Quantidade ou valor inválido.")
        return redirect(url_for("index"))

    conn = get_db()
    item = conn.execute("SELECT produto_id FROM itens_compra WHERE id=?", (item_id,)).fetchone()
    conn.execute(
        "UPDATE itens_compra SET quantidade=?, unidade=?, valor_unitario=? WHERE id=?",
        (quantidade, unidade, valor, item_id),
    )
    if item:
        conn.execute("UPDATE produtos SET unidade=? WHERE id=?", (unidade, item["produto_id"]))
    conn.commit()
    conn.close()
    atualizar_backup_automatico()
    return redirect(url_for("index"))


@app.post("/compra/<int:compra_id>/editar")
def editar_compra(compra_id):
    mercado_nome = request.form.get("mercado", "").strip()
    orcamento = parse_money(request.form.get("orcamento"))

    conn = get_db()
    compra = conn.execute(
        "SELECT * FROM compras WHERE id=? AND status='aberta'", (compra_id,)
    ).fetchone()

    if not compra:
        conn.close()
        flash("Essa compra não está mais em andamento.")
        return redirect(url_for("index"))

    mercado_id = None
    if mercado_nome:
        conn.execute("INSERT OR IGNORE INTO mercados(nome) VALUES (?)", (mercado_nome,))
        mercado = conn.execute(
            "SELECT id FROM mercados WHERE nome=? COLLATE NOCASE", (mercado_nome,)
        ).fetchone()
        mercado_id = mercado["id"]

    conn.execute(
        "UPDATE compras SET mercado_id=?, orcamento=? WHERE id=?",
        (mercado_id, orcamento, compra_id),
    )
    conn.commit()
    conn.close()
    flash("Dados da compra atualizados.")
    return redirect(url_for("index"))


@app.post("/compra/<int:compra_id>/cancelar")
def cancelar_compra(compra_id):
    conn = get_db()
    compra = conn.execute(
        "SELECT id FROM compras WHERE id=? AND status='aberta'", (compra_id,)
    ).fetchone()

    if compra:
        conn.execute("DELETE FROM compras WHERE id=?", (compra_id,))
        conn.commit()

    conn.close()
    flash("Compra cancelada.")
    return redirect(url_for("index"))


@app.post("/compra/<int:compra_id>/finalizar")
def finalizar_compra(compra_id):
    conn = get_db()
    conn.execute("UPDATE compras SET status='finalizada' WHERE id=?", (compra_id,))
    conn.commit()
    conn.close()
    flash("Compra finalizada e salva no histórico.")
    return redirect(url_for("historico"))


@app.route("/historico")
def historico():
    conn = get_db()
    expr = item_total_expr("i")
    compras = conn.execute(f"""
        SELECT c.*, m.nome AS mercado,
               COALESCE(SUM({expr}), 0) AS total,
               COUNT(i.id) AS itens
        FROM compras c
        LEFT JOIN mercados m ON m.id=c.mercado_id
        LEFT JOIN itens_compra i ON i.compra_id=c.id
        WHERE c.status='finalizada'
        GROUP BY c.id
        ORDER BY c.data DESC
    """).fetchall()
    conn.close()
    return render_template("historico.html", compras=compras)


@app.post("/historico/limpar")
def limpar_historico():
    conn = get_db()
    conn.execute("DELETE FROM compras WHERE status='finalizada'")
    conn.commit()
    conn.close()
    flash("Histórico de compras apagado.")
    return redirect(url_for("historico"))


@app.route("/historico/<int:compra_id>")
def detalhe_compra(compra_id):
    conn = get_db()
    compra = conn.execute("""
        SELECT c.*, m.nome AS mercado
        FROM compras c
        LEFT JOIN mercados m ON m.id=c.mercado_id
        WHERE c.id=?
    """, (compra_id,)).fetchone()
    expr = item_total_expr("i")
    itens = conn.execute(f"""
        SELECT i.*, p.nome AS produto, cat.nome AS categoria,
               {expr} AS subtotal
        FROM itens_compra i
        JOIN produtos p ON p.id=i.produto_id
        LEFT JOIN categorias cat ON cat.id=p.categoria_id
        WHERE i.compra_id=?
        ORDER BY i.id
    """, (compra_id,)).fetchall()
    total = purchase_total(conn, compra_id)
    conn.close()
    return render_template("detalhe_compra.html", compra=compra, itens=itens, total=total)


@app.route("/produtos")
def produtos():
    conn = get_db()
    produtos = conn.execute("""
        SELECT p.*, c.nome AS categoria,
               (SELECT i.valor_unitario
                FROM itens_compra i
                WHERE i.produto_id=p.id
                ORDER BY i.id DESC LIMIT 1) AS ultimo_preco,
               (SELECT MIN(i.valor_unitario)
                FROM itens_compra i
                WHERE i.produto_id=p.id) AS menor_preco
        FROM produtos p
        LEFT JOIN categorias c ON c.id=p.categoria_id
        WHERE p.ativo=1
        ORDER BY p.nome
    """).fetchall()
    categorias = conn.execute("SELECT * FROM categorias ORDER BY nome").fetchall()
    conn.close()
    return render_template("produtos.html", produtos=produtos, categorias=categorias, unidades=UNIDADES)


@app.post("/produtos/adicionar")
def adicionar_produto():
    nome = request.form.get("nome", "").strip()
    categoria_id = request.form.get("categoria_id") or None
    codigo = request.form.get("codigo_barras", "").strip() or None
    unidade = request.form.get("unidade", "un").strip()

    if unidade not in UNIDADES_VALIDAS:
        unidade = "un"

    if not nome:
        flash("Informe o nome do produto.")
        return redirect(url_for("produtos"))

    conn = get_db()
    existente = None

    # Primeiro tenta pelo código. Assim um produto já conhecido pelo scanner
    # nunca vira uma duplicata.
    if codigo:
        existente = conn.execute(
            "SELECT id FROM produtos WHERE codigo_barras=? LIMIT 1",
            (codigo,),
        ).fetchone()

    # Se o código é novo, tenta aproveitar um cadastro antigo com o mesmo nome
    # e apenas ensinar o código de barras a ele.
    if not existente:
        existente = conn.execute(
            "SELECT id FROM produtos WHERE nome=? COLLATE NOCASE LIMIT 1",
            (nome,),
        ).fetchone()

    if existente:
        conn.execute("""
            UPDATE produtos
            SET nome=?,
                categoria_id=?,
                codigo_barras=COALESCE(?, codigo_barras),
                unidade=?,
                ativo=1
            WHERE id=?
        """, (nome, categoria_id, codigo, unidade, existente["id"]))
        mensagem = "Produto atualizado no catálogo."
    else:
        conn.execute("""
            INSERT INTO produtos(nome, categoria_id, codigo_barras, unidade, ativo)
            VALUES (?, ?, ?, ?, 1)
        """, (nome, categoria_id, codigo, unidade))
        mensagem = "Produto cadastrado no catálogo."

    conn.commit()
    conn.close()
    atualizar_backup_automatico()
    flash(mensagem)
    return redirect(url_for("produtos"))


@app.post("/produtos/<int:produto_id>/excluir")
def excluir_produto(produto_id):
    conn = get_db()
    conn.execute("UPDATE produtos SET ativo=0 WHERE id=?", (produto_id,))
    conn.commit()
    conn.close()
    atualizar_backup_automatico()
    return redirect(url_for("produtos"))


@app.route("/configuracoes")
def configuracoes():
    conn = get_db()
    categorias = conn.execute("SELECT * FROM categorias ORDER BY nome").fetchall()
    mercados = conn.execute("SELECT * FROM mercados ORDER BY nome").fetchall()
    conn.close()
    cosmos_configurado, cosmos_user_agent, cosmos_origem = cosmos_status()
    backup_info = info_backup_produtos()
    return render_template(
        "configuracoes.html",
        categorias=categorias,
        mercados=mercados,
        cosmos_configurado=cosmos_configurado,
        cosmos_user_agent=cosmos_user_agent,
        cosmos_origem=cosmos_origem,
        backup_info=backup_info,
    )



@app.get("/configuracoes/backup/produtos")
def baixar_backup_produtos():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    nome = f"meu_mercado_produtos_{timestamp}.json"
    try:
        caminho, total = gravar_backup_produtos(nome)
        # Também atualiza a cópia automática mais recente.
        gravar_backup_produtos("produtos_automatico.json")
    except (OSError, sqlite3.Error, ValueError):
        app.logger.exception("Falha ao criar backup manual.")
        flash("Não foi possível criar o backup de produtos.")
        return redirect(url_for("configuracoes"))

    return send_file(
        caminho,
        as_attachment=True,
        download_name=nome,
        mimetype="application/json",
    )


@app.post("/configuracoes/backup/produtos/restaurar")
def restaurar_backup_produtos():
    arquivo = request.files.get("arquivo_backup")
    if not arquivo or not arquivo.filename:
        flash("Selecione um arquivo de backup JSON.")
        return redirect(url_for("configuracoes"))

    if not arquivo.filename.lower().endswith(".json"):
        flash("O backup deve ser um arquivo .json gerado pelo Meu Mercado.")
        return redirect(url_for("configuracoes"))

    try:
        # Limite simples para evitar uploads acidentais muito grandes.
        conteudo = arquivo.read(5 * 1024 * 1024 + 1)
        if len(conteudo) > 5 * 1024 * 1024:
            raise ValueError("O arquivo de backup é maior que 5 MB.")

        payload = json.loads(conteudo.decode("utf-8-sig"))
        restaurados, ignorados = restaurar_catalogo_produtos(payload)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError, sqlite3.Error, OSError) as exc:
        flash(f"Não foi possível restaurar o backup: {exc}")
        return redirect(url_for("configuracoes"))

    if ignorados:
        flash(f"Backup restaurado: {restaurados} produtos processados e {ignorados} ignorados.")
    else:
        flash(f"Backup restaurado com sucesso: {restaurados} produtos processados.")
    return redirect(url_for("configuracoes"))


@app.post("/configuracoes/cosmos")
def salvar_cosmos():
    # Se as credenciais vierem do ambiente Docker, o .env continua tendo prioridade.
    if COSMOS_API_TOKEN and COSMOS_USER_AGENT:
        flash("O Cosmos está configurado pelo arquivo .env. Para alterá-lo, edite o .env do container.")
        return redirect(url_for("configuracoes"))

    token = request.form.get("cosmos_api_token", "").strip()
    user_agent = request.form.get("cosmos_user_agent", "").strip()

    conn = get_db()
    if token:
        conn.execute(
            "INSERT INTO configuracoes_app(chave, valor) VALUES ('cosmos_api_token', ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
            (token,),
        )
    if user_agent:
        conn.execute(
            "INSERT INTO configuracoes_app(chave, valor) VALUES ('cosmos_user_agent', ?) "
            "ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
            (user_agent,),
        )
    conn.commit()
    conn.close()

    configurado, _, _ = cosmos_status()
    flash("Cosmos configurado com sucesso." if configurado else "Informe o Token e o User-Agent para ativar o Cosmos.")
    return redirect(url_for("configuracoes"))


@app.post("/configuracoes/cosmos/remover")
def remover_cosmos():
    if COSMOS_API_TOKEN and COSMOS_USER_AGENT:
        flash("O Cosmos está configurado pelo arquivo .env e não pode ser removido por esta tela.")
        return redirect(url_for("configuracoes"))

    conn = get_db()
    conn.execute("DELETE FROM configuracoes_app WHERE chave IN ('cosmos_api_token', 'cosmos_user_agent')")
    conn.commit()
    conn.close()
    flash("Integração com o Cosmos removida.")
    return redirect(url_for("configuracoes"))


@app.post("/categorias/adicionar")
def adicionar_categoria():
    nome = request.form.get("nome", "").strip()
    if nome:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO categorias(nome) VALUES (?)", (nome,))
        conn.commit()
        conn.close()
    return redirect(url_for("configuracoes"))


@app.post("/categorias/<int:categoria_id>/excluir")
def excluir_categoria(categoria_id):
    conn = get_db()
    conn.execute("UPDATE produtos SET categoria_id=NULL WHERE categoria_id=?", (categoria_id,))
    conn.execute("DELETE FROM categorias WHERE id=?", (categoria_id,))
    conn.commit()
    conn.close()
    atualizar_backup_automatico()
    return redirect(url_for("configuracoes"))


@app.post("/mercados/adicionar")
def adicionar_mercado():
    nome = request.form.get("nome", "").strip()
    if nome:
        conn = get_db()
        conn.execute("INSERT OR IGNORE INTO mercados(nome) VALUES (?)", (nome,))
        conn.commit()
        conn.close()
    return redirect(url_for("configuracoes"))


@app.post("/mercados/<int:mercado_id>/excluir")
def excluir_mercado(mercado_id):
    conn = get_db()
    conn.execute("UPDATE compras SET mercado_id=NULL WHERE mercado_id=?", (mercado_id,))
    conn.execute("DELETE FROM mercados WHERE id=?", (mercado_id,))
    conn.commit()
    conn.close()
    return redirect(url_for("configuracoes"))


@app.route("/resumo")
def resumo():
    mes = request.args.get("mes") or datetime.now().strftime("%Y-%m")
    conn = get_db()
    expr = item_total_expr("i")

    total_mes = conn.execute(f"""
        SELECT COALESCE(SUM({expr}), 0) AS total
        FROM compras c
        JOIN itens_compra i ON i.compra_id=c.id
        WHERE c.status='finalizada' AND substr(c.data,1,7)=?
    """, (mes,)).fetchone()["total"]

    por_categoria = conn.execute(f"""
        SELECT COALESCE(cat.nome, 'Sem categoria') AS categoria,
               SUM({expr}) AS total
        FROM compras c
        JOIN itens_compra i ON i.compra_id=c.id
        JOIN produtos p ON p.id=i.produto_id
        LEFT JOIN categorias cat ON cat.id=p.categoria_id
        WHERE c.status='finalizada' AND substr(c.data,1,7)=?
        GROUP BY COALESCE(cat.nome, 'Sem categoria')
        ORDER BY total DESC
    """, (mes,)).fetchall()

    conn.close()
    return render_template("resumo.html", mes=mes, total_mes=total_mes, por_categoria=por_categoria)


@app.get("/api/produtos")
def api_produtos():
    q = request.args.get("q", "").strip()
    conn = get_db()
    rows = conn.execute("""
        SELECT p.*
        FROM produtos p
        WHERE p.ativo=1 AND p.nome LIKE ?
        ORDER BY p.nome
        LIMIT 10
    """, (f"%{q}%",)).fetchall()
    result = [produto_payload(conn, row) for row in rows]
    conn.close()
    return jsonify(result)


@app.get("/api/produto/codigo/<path:codigo>")
def api_produto_codigo(codigo):
    codigo = codigo.strip()
    if not codigo or len(codigo) > 200:
        return jsonify({"encontrado": False, "erro": "Código inválido."}), 400

    conn = get_db()
    produto = conn.execute(
        "SELECT * FROM produtos WHERE codigo_barras=? AND ativo=1 LIMIT 1", (codigo,)
    ).fetchone()
    if produto:
        payload = produto_payload(conn, produto)
        conn.close()
        return jsonify({"encontrado": True, "produto": payload})
    conn.close()

    off, off_status = buscar_open_food_facts(codigo)
    if off:
        return jsonify({
            "encontrado": True,
            "produto": {
                "nome": off["nome"],
                "categoria_id": None,
                "codigo_barras": codigo,
                "unidade": "un",
                "ultimo_preco": None,
                "menor_preco": None,
                "fonte": off["fonte"],
            },
            "consultas": {"openfoodfacts": off_status, "cosmos": "nao_consultado"},
        })

    cosmos, cosmos_lookup_status = buscar_cosmos(codigo)
    if cosmos:
        return jsonify({
            "encontrado": True,
            "produto": {
                "nome": cosmos["nome"],
                "categoria_id": None,
                "codigo_barras": codigo,
                "unidade": "un",
                "ultimo_preco": None,
                "menor_preco": None,
                "fonte": cosmos["fonte"],
                "imagem": cosmos.get("imagem"),
                "peso_liquido": cosmos.get("peso_liquido"),
            },
            "consultas": {"openfoodfacts": off_status, "cosmos": cosmos_lookup_status},
        })

    return jsonify({
        "encontrado": False,
        "codigo_barras": codigo,
        "consultas": {"openfoodfacts": off_status, "cosmos": cosmos_lookup_status},
        "cosmos_configurado": cosmos_status()[0],
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
