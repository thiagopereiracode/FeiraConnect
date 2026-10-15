from flask import Flask, jsonify, request, send_from_directory
from pathlib import Path
import sqlite3, os

ROOT = Path(__file__).resolve().parent.parent
DB = Path(os.environ.get('DATABASE_PATH', str(ROOT / 'feiraconnect.db')))
app = Flask(__name__, static_folder=str(ROOT), static_url_path='')

def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con

def init_db():
    with connect() as con:
        con.executescript('''
        CREATE TABLE IF NOT EXISTS produtos(id INTEGER PRIMARY KEY AUTOINCREMENT,nome TEXT NOT NULL,categoria TEXT NOT NULL,preco REAL NOT NULL CHECK(preco>=0),estoque INTEGER NOT NULL CHECK(estoque>=0));
        CREATE TABLE IF NOT EXISTS vendas(id INTEGER PRIMARY KEY AUTOINCREMENT,produto_id INTEGER,produto_nome TEXT NOT NULL,quantidade INTEGER NOT NULL,preco_unitario REAL NOT NULL,total REAL NOT NULL,data TEXT NOT NULL DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS excedentes(id INTEGER PRIMARY KEY AUTOINCREMENT,produto TEXT NOT NULL,quantidade INTEGER NOT NULL,destino TEXT NOT NULL,observacao TEXT DEFAULT '',data TEXT NOT NULL DEFAULT (datetime('now','localtime')));
        CREATE TABLE IF NOT EXISTS fechamentos(id INTEGER PRIMARY KEY AUTOINCREMENT,data TEXT NOT NULL DEFAULT (datetime('now','localtime')),total_vendas REAL NOT NULL,dinheiro REAL NOT NULL,pix REAL NOT NULL,cartao REAL NOT NULL,observacao TEXT DEFAULT '');
        ''')

@app.get('/')
def home(): return send_from_directory(ROOT, 'index.html')
@app.get('/style.css')
def css(): return send_from_directory(ROOT, 'style.css')
@app.get('/script.js')
def js(): return send_from_directory(ROOT, 'script.js')
@app.get('/api/health')
def health(): return jsonify(projeto='FeiraConnect', status='online')
@app.get('/api/dashboard')
def dashboard():
    with connect() as c:
        p=c.execute('SELECT COUNT(*) n,COALESCE(SUM(estoque),0) unidades,COALESCE(SUM(CASE WHEN estoque<=5 THEN 1 ELSE 0 END),0) baixo FROM produtos').fetchone()
        v=c.execute("SELECT COUNT(*) n,COALESCE(SUM(total),0) total FROM vendas WHERE date(data)=date('now','localtime')").fetchone()
    return jsonify(produtos=p['n'],unidades=p['unidades'],estoque_baixo=p['baixo'],vendas_hoje=v['n'],faturamento_hoje=round(v['total'],2))
@app.get('/api/produtos')
def products():
    with connect() as c: rows=c.execute('SELECT * FROM produtos ORDER BY id DESC').fetchall()
    return jsonify([dict(x) for x in rows])
@app.post('/api/produtos')
def add_product():
    d=request.get_json(silent=True) or {}
    try:
        nome=str(d.get('nome','')).strip(); cat=str(d.get('categoria','')).strip(); price=float(d.get('preco',0)); stock=int(d.get('estoque',0))
        if not nome or not cat or price<0 or stock<0: raise ValueError()
        with connect() as c: cur=c.execute('INSERT INTO produtos(nome,categoria,preco,estoque) VALUES(?,?,?,?)',(nome,cat,price,stock)); pid=cur.lastrowid
        return jsonify(mensagem='Produto cadastrado.',id=pid),201
    except (ValueError,TypeError): return jsonify(erro='Preencha os campos corretamente.'),400
@app.delete('/api/produtos/<int:pid>')
def delete_product(pid):
    with connect() as c:
        if not c.execute('SELECT id FROM produtos WHERE id=?',(pid,)).fetchone(): return jsonify(erro='Produto não encontrado.'),404
        if c.execute('SELECT id FROM vendas WHERE produto_id=? LIMIT 1',(pid,)).fetchone(): return jsonify(erro='Produto com vendas registradas não pode ser excluído.'),409
        c.execute('DELETE FROM produtos WHERE id=?',(pid,))
    return jsonify(mensagem='Produto excluído.')
@app.get('/api/vendas')
def sales():
    with connect() as c: rows=c.execute('SELECT * FROM vendas ORDER BY id DESC LIMIT 100').fetchall()
    return jsonify([dict(x) for x in rows])
@app.post('/api/vendas')
def add_sale():
    d=request.get_json(silent=True) or {}
    try: pid=int(d.get('produto_id',0)); qty=int(d.get('quantidade',0))
    except (ValueError,TypeError): return jsonify(erro='Dados inválidos.'),400
    if qty<=0: return jsonify(erro='A quantidade deve ser maior que zero.'),400
    with connect() as c:
        p=c.execute('SELECT * FROM produtos WHERE id=?',(pid,)).fetchone()
        if not p: return jsonify(erro='Produto não encontrado.'),404
        if p['estoque']<qty: return jsonify(erro='Estoque insuficiente.'),400
        total=round(p['preco']*qty,2)
        c.execute('INSERT INTO vendas(produto_id,produto_nome,quantidade,preco_unitario,total) VALUES(?,?,?,?,?)',(pid,p['nome'],qty,p['preco'],total))
        c.execute('UPDATE produtos SET estoque=estoque-? WHERE id=?',(qty,pid))
    return jsonify(mensagem='Venda registrada.',total=total),201
@app.get('/api/excedentes')
def surplus():
    with connect() as c: rows=c.execute('SELECT * FROM excedentes ORDER BY id DESC LIMIT 100').fetchall()
    return jsonify([dict(x) for x in rows])
@app.post('/api/excedentes')
def add_surplus():
    d=request.get_json(silent=True) or {}; product=str(d.get('produto','')).strip(); dest=str(d.get('destino','')).strip(); obs=str(d.get('observacao','')).strip()
    try: qty=int(d.get('quantidade',0))
    except (ValueError,TypeError): qty=0
    if not product or not dest or qty<=0: return jsonify(erro='Informe produto, quantidade e destino.'),400
    with connect() as c: c.execute('INSERT INTO excedentes(produto,quantidade,destino,observacao) VALUES(?,?,?,?)',(product,qty,dest,obs))
    return jsonify(mensagem='Registro de excedente salvo.'),201
@app.get('/api/fechamentos')
def closings():
    with connect() as c: rows=c.execute('SELECT * FROM fechamentos ORDER BY id DESC LIMIT 100').fetchall()
    return jsonify([dict(x) for x in rows])
@app.post('/api/fechamentos')
def add_closing():
    d=request.get_json(silent=True) or {}
    try: cash=float(d.get('dinheiro',0)); pix=float(d.get('pix',0)); card=float(d.get('cartao',0))
    except (ValueError,TypeError): return jsonify(erro='Valores inválidos.'),400
    if min(cash,pix,card)<0: return jsonify(erro='Os valores não podem ser negativos.'),400
    obs=str(d.get('observacao','')).strip()
    with connect() as c:
        total=c.execute("SELECT COALESCE(SUM(total),0) t FROM vendas WHERE date(data)=date('now','localtime')").fetchone()['t']
        cur=c.execute('INSERT INTO fechamentos(total_vendas,dinheiro,pix,cartao,observacao) VALUES(?,?,?,?,?)',(total,cash,pix,card,obs))
    return jsonify(mensagem='Fechamento registrado.',id=cur.lastrowid,total_vendas=round(total,2)),201

init_db()
if __name__ == '__main__': app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
