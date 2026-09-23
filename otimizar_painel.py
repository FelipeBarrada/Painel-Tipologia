#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
otimizar_painel.py
------------------
Reduz o tamanho do painel de tipologia municipal de ~15,4 GB para ~155 MB,
sem perder nenhum dado, permitindo a publicacao no GitHub Pages.

COMO FUNCIONA
  Hoje cada arquivo data/municipios/<cod>.js repete, para cada uma das ~9.000
  variaveis, 10 campos de metadados (k, v, d, b, g, f, c, r, w, q) que sao
  IDENTICOS em todos os 5.571 municipios. So o campo "x" (o valor) muda.
  O mesmo vale para data/modelo_municipios/<cod>.js, cujos metadados ja estao
  integralmente em data/modelo_catalogo.js.

  O script extrai esses metadados para um dicionario global unico e grava, por
  municipio, apenas [indice_no_dicionario, valor]. O resultado e comprimido em
  gzip e lido no navegador via fetch + DecompressionStream.

  O painel remonta ("reidrata") o objeto exatamente na forma original antes de
  chamar window.__entregaMunicipio, de modo que NENHUMA outra parte do seu
  codigo de renderizacao precisa mudar.

USO
  python otimizar_painel.py                  # executa a conversao
  python otimizar_painel.py --dry-run        # so estima, nao escreve nada
  python otimizar_painel.py --manter-origem  # nao remove as pastas antigas

Rode a partir da pasta 'painelipologia-publicar' (a que contem index.html).
O script cria um backup do index.html antes de altera-lo.
"""

import argparse
import gzip
import json
import re
import shutil
import sys
import time
from pathlib import Path

META = ["k", "v", "d", "b", "g", "f", "c", "r", "w", "q"]
MODELO_VAR = ["valor_modelo", "valor_observado", "valor_exibido",
              "tem_valor_observado", "observacao", "transformacao"]

MARCADOR = "<!-- PAINEL_LOADER_OTIMIZADO -->"


# --------------------------------------------------------------------------
# utilidades
# --------------------------------------------------------------------------
def extrai_json(texto, prefixos):
    """Extrai o objeto JSON de um arquivo no formato callback(...) ou VAR=..."""
    for p in prefixos:
        if texto.lstrip().startswith(p):
            corte = texto.index("(") + 1 if p.endswith("(") else texto.index("=") + 1
            fim = texto.rindex(")") if p.endswith("(") else len(texto.rstrip().rstrip(";"))
            return json.loads(texto[corte:fim])
    # fallback generico
    if "(" in texto and texto.rstrip().endswith((");", ")")):
        return json.loads(texto[texto.index("(") + 1:texto.rindex(")")])
    return json.loads(texto[texto.index("=") + 1:].rstrip().rstrip(";"))


def humano(n):
    for u in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:,.1f} {u}".replace(",", ".")
        n /= 1024
    return f"{n:,.1f} TB"


def tamanho_pasta(p):
    if not p.exists():
        return 0
    return sum(f.stat().st_size for f in p.rglob("*") if f.is_file())


# --------------------------------------------------------------------------
# conversao
# --------------------------------------------------------------------------
def converter(base, dry_run=False, manter_origem=False):
    data = base / "data"
    dir_mun = data / "municipios"
    dir_mod = data / "modelo_municipios"
    destino = data / "mun"

    if not dir_mun.exists():
        sys.exit(f"ERRO: nao encontrei {dir_mun}. Rode o script na pasta do painel.")

    arquivos = sorted(dir_mun.glob("*.js"))
    if not arquivos:
        sys.exit(f"ERRO: nenhum arquivo .js em {dir_mun}.")

    antes = tamanho_pasta(data)
    print(f"Pasta data/ atual .......... {humano(antes)}")
    print(f"Municipios encontrados ..... {len(arquivos):,}".replace(",", "."))
    print()

    if not dry_run:
        destino.mkdir(parents=True, exist_ok=True)

    dicionario = {}      # tupla de metadados -> indice
    ordem_dic = []       # lista na ordem dos indices
    total_saida = 0
    erros = []
    t0 = time.time()

    for i, arq in enumerate(arquivos, 1):
        cod = arq.stem
        try:
            bruto = arq.read_text(encoding="utf-8")
            payload = extrai_json(bruto, ["window.__entregaMunicipio("])
        except Exception as e:
            erros.append(f"{cod}: {e}")
            continue

        variaveis = payload.get("variaveis", [])
        idx, valores = [], []
        for r in variaveis:
            chave = tuple(r.get(m) for m in META) + (r.get("a"),)
            pos = dicionario.get(chave)
            if pos is None:
                pos = len(ordem_dic)
                dicionario[chave] = pos
                ordem_dic.append(list(chave))
            idx.append(pos)
            valores.append(r.get("x"))

        saida = {"c": cod, "i": idx, "x": valores}

        # camada do modelo (ICE/IEG), se existir
        arq_mod = dir_mod / f"{cod}.js"
        if arq_mod.exists():
            try:
                pm = extrai_json(arq_mod.read_text(encoding="utf-8"),
                                 ["window.__entregaModeloMunicipio("])
                saida["m"] = [
                    [it.get("item_id")] + [it.get(c) for c in MODELO_VAR]
                    for it in pm.get("itens", [])
                ]
            except Exception as e:
                erros.append(f"modelo {cod}: {e}")

        blob = gzip.compress(
            json.dumps(saida, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
            9,
        )
        total_saida += len(blob)
        if not dry_run:
            (destino / f"{cod}.json.gz").write_bytes(blob)

        if i % 500 == 0 or i == len(arquivos):
            pct = 100 * i / len(arquivos)
            print(f"  {i:>5,}/{len(arquivos):,}  ({pct:5.1f}%)  "
                  f"acumulado {humano(total_saida)}".replace(",", "."))

    # dicionario global
    dic_json = json.dumps(ordem_dic, ensure_ascii=False, separators=(",", ":"))
    dic_blob = gzip.compress(
        f"window.__PAINEL_DIC={dic_json};".encode("utf-8"), 9)
    if not dry_run:
        (data / "dicionario.json.gz").write_bytes(dic_blob)
    total_saida += len(dic_blob)

    print()
    print(f"Entradas no dicionario ..... {len(ordem_dic):,}".replace(",", "."))
    print(f"Tempo ...................... {time.time() - t0:.0f}s")
    if erros:
        print(f"AVISOS ({len(erros)}): mostrando os 5 primeiros")
        for e in erros[:5]:
            print("   -", e)

    if dry_run:
        print()
        print(f"[dry-run] Saida estimada ... {humano(total_saida)}")
        print("[dry-run] Nada foi escrito.")
        return

    # patch no index.html
    aplicar_loader(base)

    # remocao das pastas antigas
    if not manter_origem:
        for d in (dir_mun, dir_mod):
            if d.exists():
                shutil.rmtree(d)
        print("Pastas originais removidas (municipios/, modelo_municipios/).")
    else:
        print("Pastas originais mantidas (--manter-origem).")

    depois = tamanho_pasta(data)
    print()
    print("=" * 58)
    print(f"  ANTES  {humano(antes)}")
    print(f"  DEPOIS {humano(depois)}")
    if depois:
        print(f"  Reducao de {100 * (1 - depois / antes):.1f}%  "
              f"({antes / depois:.0f}x menor)")
    print("=" * 58)
    print()
    print("Proximos passos:")
    print("  1. Abra o index.html localmente e teste alguns municipios.")
    print("     (use um servidor local: python -m http.server)")
    print("  2. git add -A && git commit -m 'Otimiza dados do painel'")
    print("  3. git push")


# --------------------------------------------------------------------------
# loader injetado no index.html
# --------------------------------------------------------------------------
LOADER = MARCADOR + """
    <script>
    (function () {
      // Carrega o dicionario global uma unica vez e reidrata os dados de cada
      // municipio na forma original, preservando window.__entregaMunicipio e
      // window.__entregaModeloMunicipio.
      const META = ["k","v","d","b","g","f","c","r","w","q"];
      const MODELO_VAR = ["valor_modelo","valor_observado","valor_exibido",
                          "tem_valor_observado","observacao","transformacao"];
      let DIC = null, DIC_PROMISE = null;
      const CACHE = new Map();

      async function baixarGz(url) {
        const resp = await fetch(url);
        if (!resp.ok) throw new Error(url + " -> " + resp.status);
        const buf = await resp.arrayBuffer();
        // O GitHub Pages pode entregar o .gz ja descomprimido (Content-Encoding).
        const b = new Uint8Array(buf);
        if (b[0] === 0x1f && b[1] === 0x8b) {
          const ds = new DecompressionStream("gzip");
          const txt = await new Response(
            new Blob([buf]).stream().pipeThrough(ds)
          ).text();
          return txt;
        }
        return new TextDecoder("utf-8").decode(buf);
      }

      function carregarDic() {
        if (DIC) return Promise.resolve(DIC);
        if (!DIC_PROMISE) {
          DIC_PROMISE = baixarGz("data/dicionario.json.gz").then((t) => {
            const i = t.indexOf("=");
            DIC = JSON.parse(t.slice(i + 1).replace(/;\\s*$/, ""));
            return DIC;
          });
        }
        return DIC_PROMISE;
      }

      async function carregarMunicipio(cod) {
        if (CACHE.has(cod)) return CACHE.get(cod);
        const [dic, txt] = await Promise.all([
          carregarDic(),
          baixarGz("data/mun/" + cod + ".json.gz"),
        ]);
        const raw = JSON.parse(txt);

        // reidrata as variaveis na forma original
        const variaveis = raw.i.map((pos, n) => {
          const d = dic[pos], o = {};
          for (let k = 0; k < META.length; k++) o[META[k]] = d[k];
          o.a = d[META.length];
          o.x = raw.x[n];
          return o;
        });

        // reidrata a camada do modelo a partir do catalogo ja carregado
        let itens = [];
        if (raw.m && window.PAINEL_MODELO_CATALOGO) {
          const cat = {};
          window.PAINEL_MODELO_CATALOGO.forEach((c) => (cat[c.item_id] = c));
          itens = raw.m.map((linha) => {
            const base = cat[linha[0]] || {};
            const it = Object.assign({}, base, { item_id: linha[0] });
            MODELO_VAR.forEach((campo, k) => {
              const v = linha[k + 1];
              if (v !== null && v !== undefined) it[campo] = v;
            });
            return it;
          });
        }

        const pacote = {
          municipio: Object.assign(
            {}, window.PAINEL_RESUMO[cod] || {}, { cd_munic: cod }
          ),
          variaveis: variaveis,
          modelo_itens: [],
          __itens: itens,
        };
        CACHE.set(cod, pacote);
        return pacote;
      }

      // Substitui a injecao de <script> por fetch, mantendo as callbacks.
      window.__carregarMunicipio = function (cod) {
        carregarMunicipio(cod)
          .then((p) => window.__entregaMunicipio(p))
          .catch((e) => {
            console.error(e);
            document.querySelector("#side").textContent =
              "Detalhe nao encontrado.";
          });
      };
      window.__carregarModeloMunicipio = function (cod) {
        carregarMunicipio(cod)
          .then((p) =>
            window.__entregaModeloMunicipio({ cd_munic: cod, itens: p.__itens })
          )
          .catch((e) => console.error(e));
      };
    })();
    </script>
"""


def aplicar_loader(base):
    idx = base / "index.html"
    if not idx.exists():
        print("AVISO: index.html nao encontrado; o loader nao foi aplicado.")
        return

    html = idx.read_text(encoding="utf-8")
    if MARCADOR in html:
        print("index.html ja contem o loader otimizado; nada a fazer.")
        return

    shutil.copy2(idx, base / "index.html.bak")

    # 1) troca a injecao de <script> do municipio por fetch
    alvo_mun = re.compile(
        r'S\.innerHTML\s*=\s*"Carregando\.\.\.";.*?document\.body\.appendChild\(z\);',
        re.S,
    )
    novo_mun = (
        'S.innerHTML = "Carregando...";\n'
        '        window.__carregarMunicipio(c);'
    )
    html, n1 = alvo_mun.subn(novo_mun, html, count=1)

    # 2) troca a injecao de <script> da camada do modelo por fetch
    alvo_mod = re.compile(
        r'const script = document\.createElement\("script"\);\s*'
        r'script\.id = "modelmunjs";.*?document\.body\.appendChild\(script\);',
        re.S,
    )
    novo_mod = "window.__carregarModeloMunicipio(code);"
    html, n2 = alvo_mod.subn(novo_mod, html, count=1)

    # 3) injeta o loader logo apos o catalogo do modelo
    ancora = '<script src="data/modelo_catalogo.js"></script>'
    if ancora in html:
        html = html.replace(ancora, ancora + "\n" + LOADER, 1)
        n3 = 1
    else:
        html = html.replace("</body>", LOADER + "\n  </body>", 1)
        n3 = 1

    idx.write_text(html, encoding="utf-8")
    print(f"index.html atualizado (municipio={n1}, modelo={n2}, loader={n3}).")
    print("Backup salvo em index.html.bak")
    if n1 == 0 or n2 == 0:
        print("AVISO: algum trecho nao foi encontrado. Confira o index.html.")


# --------------------------------------------------------------------------
if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Otimiza o painel para o GitHub Pages.")
    ap.add_argument("--dry-run", action="store_true",
                    help="apenas estima o resultado, sem escrever nada")
    ap.add_argument("--manter-origem", action="store_true",
                    help="nao remove as pastas municipios/ e modelo_municipios/")
    ap.add_argument("--pasta", default=".",
                    help="pasta do painel (padrao: diretorio atual)")
    a = ap.parse_args()
    converter(Path(a.pasta).resolve(), a.dry_run, a.manter_origem)
