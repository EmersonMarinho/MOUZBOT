# 🚀 Como Rodar o Bot

## Passo 1: Configurar o Token do Discord

1. **Abra o arquivo `.env`** na raiz do projeto
2. **Substitua** `seu_token_do_bot_aqui` pelo token real do seu bot
3. **Salve o arquivo**

**Exemplo:**
```env
DISCORD_TOKEN=MTIzNDU2Nzg5MDEyMzQ1Njc4OQ.ABCdef.GHIjkl-MNOpqr-STUvwx-YZ1234
```

> 💡 **Como obter o token:**
> - Acesse https://discord.com/developers/applications
> - Selecione sua aplicação
> - Vá em "Bot" > Copie o token

## Passo 2: Verificar Configurações (Opcional)

### Banco de Dados
- **PostgreSQL (Neon)**: Já configurado no `.env` ✅
- **SQLite**: Funciona automaticamente se não houver `DATABASE_URL`
- **MongoDB**: Descomente as linhas no `.env` se quiser usar

## Passo 3: Rodar o Bot

### Opção 1: Terminal/PowerShell
```bash
python main.py
```

### Opção 2: Python direto
```bash
python -m main
```

## Passo 4: Verificar se Funcionou

Você deve ver mensagens como:
```
NomeDoBot#1234 está online!
Sincronizados X comando(s)
```

## ⚠️ Problemas Comuns

### Erro: "DISCORD_TOKEN não encontrado"
- Verifique se o arquivo `.env` existe
- Verifique se o token está correto (sem espaços extras)

### Erro: "ModuleNotFoundError"
- Instale as dependências: `pip install -r requirements.txt`

### Bot não responde aos comandos
- Aguarde alguns minutos (sincronização de comandos)
- Verifique se o bot está online no servidor
- Verifique as permissões do bot

### Erro de conexão com banco de dados
- Verifique se a `DATABASE_URL` está correta
- Para SQLite, o banco será criado automaticamente

## 📝 Notas Importantes

- O bot precisa estar **online** para funcionar
- Mantenha o terminal aberto enquanto o bot estiver rodando
- Para parar o bot, pressione `Ctrl+C` no terminal
- Para rodar em background, use um gerenciador de processos (PM2, screen, etc.)

## 🔄 Próximos Passos

1. ✅ Configure o token no `.env`
2. ✅ Execute `python main.py`
3. ✅ Teste os comandos no Discord
4. ✅ Configure permissões se necessário

