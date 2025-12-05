# 📊 Configuração do Google Sheets para Censo

Este guia explica como configurar a integração automática com Google Sheets para o sistema de censo.

## 📋 Pré-requisitos

1. Conta no Google Cloud Platform
2. Uma planilha no Google Sheets criada
3. Python 3.8 ou superior

## 🔧 Passo a Passo

### 1. Instalar Dependências

```bash
pip install gspread>=5.12.0 google-auth>=2.23.4
```

Ou instale todas as dependências:

```bash
pip install -r requirements.txt
```

### 2. Criar Projeto no Google Cloud

1. Acesse https://console.cloud.google.com/
2. Clique em "Selecionar projeto" → "Novo projeto"
3. Dê um nome ao projeto (ex: "Discord Bot Censo")
4. Clique em "Criar"

### 3. Ativar Google Sheets API

1. No menu lateral, vá em **APIs e Serviços** → **Biblioteca**
2. Procure por "Google Sheets API"
3. Clique em "Ativar"

### 4. Criar Service Account

1. Vá em **IAM & Admin** → **Service Accounts**
2. Clique em **+ Criar Service Account**
3. Preencha:
   - **Nome:** Discord Bot Censo
   - **Descrição:** Service account para integração com Google Sheets
4. Clique em **Criar e continuar**
5. Pule a etapa de permissões (Role) e clique em **Concluir**

### 5. Baixar Credenciais

1. Clique na Service Account criada
2. Vá na aba **Chaves**
3. Clique em **Adicionar chave** → **Criar nova chave**
4. Selecione **JSON**
5. Clique em **Criar**
6. O arquivo JSON será baixado automaticamente
7. **Renomeie o arquivo para `credentials.json`**
8. **Mova o arquivo para a raiz do projeto** (mesma pasta do `main.py`)

### 6. Compartilhar Planilha

1. Abra o arquivo `credentials.json` baixado
2. Procure pelo campo `"client_email"` (algo como `nome@projeto.iam.gserviceaccount.com`)
3. Abra sua planilha do Google Sheets
4. Clique em **Compartilhar** (botão no canto superior direito)
5. Cole o e-mail da Service Account
6. Dê permissão de **Editor**
7. Clique em **Enviar**

### 7. Obter ID da Planilha

O ID da planilha está na URL:

```
https://docs.google.com/spreadsheets/d/ID_AQUI/edit
```

Copie o `ID_AQUI` (é uma string longa de letras e números).

### 8. Configurar Variáveis de Ambiente

Adicione no arquivo `.env`:

```env
# Google Sheets
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_SPREADSHEET_ID=ID_DA_PLANILHA_AQUI
GOOGLE_SHEETS_WORKSHEET_NAME=Censo
GOOGLE_SHEETS_CREDENTIALS_PATH=credentials.json
```

**Exemplo:**
```env
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_SPREADSHEET_ID=1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms
GOOGLE_SHEETS_WORKSHEET_NAME=Censo
GOOGLE_SHEETS_CREDENTIALS_PATH=credentials.json
```

### 9. Estrutura da Planilha

A planilha será criada automaticamente com as seguintes colunas:

| Data/Hora | Discord User | Nome Família | Personagem | Classe | GS | Link Gear | Observações |
|-----------|--------------|--------------|------------|--------|----|-----------|-------------|

**Nota:** Se a worksheet (aba) não existir, ela será criada automaticamente com os cabeçalhos.

## ✅ Testar

1. Reinicie o bot
2. Use `/preencher_censo` para preencher um censo
3. Verifique se os dados aparecem na planilha

## 🔒 Segurança

- **NUNCA** commite o arquivo `credentials.json` no Git
- O arquivo já está no `.gitignore` por padrão
- Mantenha o arquivo seguro e não compartilhe

## 🐛 Troubleshooting

### Erro: "File not found: credentials.json"
- Verifique se o arquivo está na raiz do projeto
- Verifique o caminho em `GOOGLE_SHEETS_CREDENTIALS_PATH`

### Erro: "Permission denied"
- Verifique se compartilhou a planilha com o e-mail da Service Account
- Verifique se deu permissão de **Editor** (não apenas Visualizador)

### Erro: "Spreadsheet not found"
- Verifique se o `GOOGLE_SHEETS_SPREADSHEET_ID` está correto
- Verifique se a planilha foi compartilhada corretamente

### Dados não aparecem na planilha
- Verifique os logs do bot para erros
- Verifique se `GOOGLE_SHEETS_ENABLED=true` no `.env`
- Verifique se as bibliotecas estão instaladas: `pip install gspread google-auth`

## 📝 Notas

- Os dados são enviados **automaticamente** quando alguém preenche o censo
- Se houver erro ao enviar para Google Sheets, o censo ainda será salvo no banco de dados
- Erros no envio para Sheets são apenas logados, não aparecem para o usuário
- A primeira linha da planilha será preenchida automaticamente com cabeçalhos se não existirem

