# Bot Discord - Gearscore BDO

Bot do Discord para gerenciar e atualizar o gearscore dos jogadores de Black Desert Online.

## 🚀 Funcionalidades

- ✅ Atualizar gearscore com AP, AAP, DP, Link Gear
- 📊 Visualizar gearscore dos seus personagens
- 🏆 Ranking de gearscore
- 🎭 Lista de classes do BDO
- 📨 Enviar gearscore via DM (mensagem direta)
- 💬 Enviar DMs para usuários (apenas administradores)
- 💾 Dados salvos em banco de dados (SQLite, PostgreSQL ou MongoDB)

## 📋 Requisitos

- Python 3.8 ou superior
- Conta no Discord Developer Portal
- Token do bot Discord

## 🔧 Instalação

1. **Clone ou baixe este repositório**

2. **Instale as dependências:**
```bash
pip install -r requirements.txt
```

3. **Crie um arquivo `.env` na raiz do projeto:**
```
DISCORD_TOKEN=seu_token_do_bot_aqui
DATABASE_URL=sua_url_de_conexao (opcional)

# Configuração de permissões para DM em massa (opcional)
# IDs dos cargos que podem usar comandos de DM em massa (separados por vírgula)
# ALLOWED_DM_ROLES=123456789012345678,987654321098765432
```

4. **Obtenha o token do bot:**
   - Acesse https://discord.com/developers/applications
   - Crie uma nova aplicação ou selecione uma existente
   - Vá em "Bot" e copie o token
   - Cole o token no arquivo `.env`

5. **Configure as permissões do bot:**
   - No Discord Developer Portal, vá em "OAuth2" > "URL Generator"
   - **Scopes (obrigatórias):**
     - `bot`
     - `applications.commands`
   - **Bot Permissions:**
     - `Send Messages` - Enviar mensagens
     - `Embed Links` - Enviar embeds
     - `Read Message History` - Ler histórico
     - `View Channels` - Ver canais
     - `View Server Members` - Ver membros (necessário para DM em massa)
   - Use a URL gerada para adicionar o bot ao seu servidor
   
   **Importante:** No Developer Portal, vá em "Bot" e habilite os seguintes **Privileged Gateway Intents:**
     - ✅ `MESSAGE CONTENT INTENT` (obrigatório)
     - ✅ `SERVER MEMBERS INTENT` (necessário para ver membros e cargos)
     - ✅ `PRESENCE INTENT` (necessário para ver status online/offline)
   
   **Nota para bots privados:**
   - Se você marcou o bot como privado (recomendado), você verá um aviso sobre "link de autorização padrão"
   - Isso é normal! A URL ainda funciona para você adicionar o bot aos seus próprios servidores
   - Bots privados são mais seguros e só podem ser adicionados pelo dono da aplicação
   
   **Permissões para DMs:**
   - O bot pode enviar DMs automaticamente (não precisa de permissão especial)
   - Certifique-se de que o bot não está bloqueado pelos usuários
   - Usuários precisam ter DMs habilitadas para receber mensagens
   
   **Configuração de Cargos para DM em Massa:**
   - Por padrão, apenas administradores podem usar comandos de DM em massa
   - Para permitir outros cargos, adicione no arquivo `.env`:
     ```
     ALLOWED_DM_ROLES=ID_DO_CARGO_1,ID_DO_CARGO_2
     ```
   - Para obter o ID de um cargo: Ative o Modo Desenvolvedor no Discord (Configurações > Avançado > Modo Desenvolvedor) e clique com botão direito no cargo > Copiar ID

## 🎮 Comandos Disponíveis

### Comandos Slash (/) - Use digitando `/` no Discord

### `/atualizar_gearscore`
Atualiza o gearscore do seu personagem.

**Parâmetros:**
- `nome_familia` - Nome da família do personagem
- `nome_personagem` - Nome do personagem
- `classe_pvp` - Classe PVP (deve ser uma das classes válidas)
- `ap` - Attack Power (número inteiro)
- `aap` - Awakened Attack Power (número inteiro)
- `dp` - Defense Power (número inteiro)
- `linkgear` - Link do gear (opcional)

**Exemplo:**
```
/atualizar_gearscore nome_familia:MeuNome nome_personagem:MeuChar classe_pvp:Guerreiro ap:300 aap:280 dp:400 linkgear:https://example.com/gear
```

### `/ver_gearscore`
Visualiza o gearscore do seu personagem.

**Parâmetros:**
- `nome_personagem` - Nome do personagem (opcional - mostra todos se não especificado)

**Exemplo:**
```
/ver_gearscore nome_personagem:MeuChar
```

### `/classes_bdo`
Lista todas as classes disponíveis do Black Desert Online.

### `/ranking_gearscore`
Mostra o ranking dos top 10 gearscores (ordenado por AP + AAP + DP).

### `/gearscore_dm`
Envia seu gearscore via mensagem direta (DM).

**Parâmetros:**
- `nome_personagem` - Nome do personagem (opcional - mostra todos se não especificado)

**Exemplo:**
```
/gearscore_dm nome_personagem:MeuChar
```

### `/enviar_dm` (Apenas Administradores ou Cargos Autorizados)
Envia uma mensagem direta (DM) para um usuário específico.

**Parâmetros:**
- `usuario` - Usuário que receberá a mensagem
- `mensagem` - Mensagem a ser enviada

**Exemplo:**
```
/enviar_dm usuario:@Usuario mensagem:Olá! Seu gearscore foi atualizado.
```

### `/dm_cargo` (Apenas Administradores ou Cargos Autorizados)
Envia DM em massa para todos os membros com um cargo específico.

**Parâmetros:**
- `cargo` - Cargo que receberá a mensagem
- `mensagem` - Mensagem a ser enviada

**Exemplo:**
```
/dm_cargo cargo:@Moderadores mensagem:Reunião importante hoje às 20h!
```

### `/dm_online` (Apenas Administradores ou Cargos Autorizados)
Envia DM em massa para todos os membros online no momento.

**Parâmetros:**
- `mensagem` - Mensagem a ser enviada

**Exemplo:**
```
/dm_online mensagem:Evento começando agora! Venha participar!
```

### `/dm_todos` (Apenas Administradores ou Cargos Autorizados)
Envia DM em massa para todos os membros do servidor.

**Parâmetros:**
- `mensagem` - Mensagem a ser enviada

**Exemplo:**
```
/dm_todos mensagem:Anúncio importante para todos os membros!
```

**⚠️ Atenção:** Comandos de DM em massa podem levar alguns minutos dependendo do número de membros.

## 📊 Estrutura do Banco de Dados

O bot utiliza SQLite para armazenar os dados. A tabela `gearscore` contém:

- `id` - ID único
- `user_id` - ID do usuário do Discord
- `family_name` - Nome da família
- `character_name` - Nome do personagem
- `class_pvp` - Classe PVP
- `ap` - Attack Power
- `aap` - Awakened Attack Power
- `dp` - Defense Power
- `linkgear` - Link do gear
- `updated_at` - Data da última atualização

## 🎭 Classes Disponíveis

- Guerreiro
- Ranger
- Feiticeira
- Berserker
- Valkyrie
- Mago
- Tamer
- Musa
- Maehwa
- Ninja
- Kunoichi
- Místico
- Lahn
- Arqueiro
- Shai
- Guardião
- Hashiashin
- Nova
- Sage
- Corsair
- Drakania
- Woosa
- Maegu
- Scholar

## 🚀 Como Executar

```bash
python main.py
```

O bot ficará online e pronto para receber comandos no Discord!

## 📝 Notas

- O banco de dados será criado automaticamente na primeira execução
- Cada usuário pode ter múltiplos personagens
- O gearscore é atualizado automaticamente quando você usa o comando `/atualizar_gearscore`
- Os dados são salvos localmente no arquivo `bdo_gearscore.db`

## 🛠️ Troubleshooting

**Bot não responde aos comandos:**
- Verifique se o token está correto no arquivo `.env`
- Certifique-se de que o bot tem permissões no servidor
- Aguarde alguns minutos após adicionar o bot (sincronização de comandos)

**Erro ao atualizar gearscore:**
- Verifique se a classe está na lista de classes válidas
- Certifique-se de que os valores numéricos são positivos

**Bot não envia DMs:**
- Verifique se o usuário não bloqueou o bot
- Certifique-se de que o usuário tem DMs habilitadas nas configurações do Discord
- Alguns servidores podem ter restrições de DM

## 📄 Licença

Este projeto é de código aberto e está disponível para uso pessoal.

