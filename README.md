# 🛒 Meu Mercado

Aplicativo web para controle de compras de mercado, desenvolvido em **Python, Flask e SQLite**, com interface responsiva para computadores e dispositivos móveis.

O projeto permite criar compras, acompanhar o orçamento, gerenciar produtos, consultar histórico, utilizar códigos de barras e manter backups do catálogo de produtos.

[![Python](https://img.shields.io/badge/Python-3.x-blue?logo=python)](https://www.python.org/)
[![Flask](https://img.shields.io/badge/Flask-Web%20Framework-black?logo=flask)](https://flask.palletsprojects.com/)
[![SQLite](https://img.shields.io/badge/SQLite-Database-blue?logo=sqlite)](https://www.sqlite.org/)
[![Docker](https://img.shields.io/badge/Docker-Container-blue?logo=docker)](https://www.docker.com/)

---

## ✨ Recursos

- 🛒 Criação e gerenciamento de compras
- 💰 Controle de orçamento
- 🧮 Cálculo automático do total da compra
- 📦 Catálogo de produtos
- 🏷️ Categorias e unidades de medida
- ⚖️ Suporte a produtos vendidos por peso ou unidade
- 📷 Leitura de códigos de barras pela câmera
- 🔎 Busca de produtos por código de barras
- 🌐 Integração com Open Food Facts
- 🔗 Integração opcional com Bluesoft Cosmos
- 📜 Histórico de compras
- 📊 Resumo das compras
- 💾 Backup automático do catálogo de produtos
- ♻️ Restauração de produtos a partir de backup
- 📱 Interface responsiva
- 🐳 Execução através de Docker
- 🏠 Compatível com ambientes self-hosted, incluindo ZimaOS

---

## 🛠️ Tecnologias

- **Python**
- **Flask**
- **SQLite**
- **HTML5**
- **CSS3**
- **JavaScript**
- **Docker / Docker Compose**
- **ZXing Browser**
- **Open Food Facts**
- **Bluesoft Cosmos** (opcional)

---

## 📱 Interface

### 🛒 Tela de compra

A tela de compra permite adicionar produtos, informar valores e acompanhar o total da compra em tempo real.

<p align="center">
  <img src="docs/images/01-tela-de-compra.png" width="300">
</p>

---

### 🏁 Iniciando uma compra

O fluxo de início de compras permite definir o mercado e o orçamento antes de começar a adicionar os produtos.

<p align="center">
  <img src="docs/images/01-tela-de-iniciar-compras1.png" width="300">
  <img src="docs/images/01-tela-de-iniciar-compras2.png" width="300">
  <img src="docs/images/01-tela-de-iniciar-compras3.png" width="300">
</p>

---

### 📜 Histórico

O histórico permite consultar compras realizadas anteriormente e acompanhar os registros das compras.

<p align="center">
  <img src="docs/images/02-tela-de-historico.png" width="300">
</p>

---

### 📦 Produtos

O catálogo de produtos permite cadastrar, consultar e organizar os produtos utilizados nas compras.

<p align="center">
  <img src="docs/images/03-tela-de-produtos1.png" width="300">
  <img src="docs/images/03-tela-de-produtos2.png" width="300">
</p>

---

### ⚙️ Ajustes

A área de ajustes concentra as configurações e opções de gerenciamento do aplicativo.

<p align="center">
  <img src="docs/images/04-tela-de-ajustes1.png" width="300">
  <img src="docs/images/04-tela-de-ajustes2.png" width="300">
  <img src="docs/images/04-tela-de-ajustes3.png" width="300">
</p>

---

### 📊 Resumo

A tela de resumo apresenta informações consolidadas sobre as compras e os gastos.

<p align="center">
  <img src="docs/images/04-tela-de-resumo.png" width="300">
</p>

---

## 🚀 Instalação

O Meu Mercado pode ser executado localmente ou em um servidor utilizando Docker.

### 📋 Pré-requisitos

Para executar o projeto com Docker, é necessário ter instalado:

- Docker
- Docker Compose

---

### 📥 1. Clone o repositório

```bash
git clone https://github.com/Jhonata830/Meu-Mercado.git
cd Meu-Mercado
