"""
Versão do banco de dados usando PostgreSQL (Supabase, Railway, Neon, etc.)
Instale: pip install psycopg2-binary
"""
import psycopg2
import os
from config import DATABASE_URL

class Database:
    def __init__(self):
        self.db_url = DATABASE_URL
        self.init_database()
    
    def get_connection(self):
        """Retorna uma conexão com o banco de dados PostgreSQL"""
        return psycopg2.connect(self.db_url)
    
    def init_database(self):
        """Inicializa o banco de dados criando a tabela se não existir"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Verificar se a tabela gearscore existe
            cursor.execute('''
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'gearscore'
                )
            ''')
            result = cursor.fetchone()
            table_exists = result[0] if result else False
            
            # Verificar e migrar tabela gearscore se necessário
            has_character_name = None
            if table_exists:
                try:
                    cursor.execute('''
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = 'gearscore' AND column_name = 'character_name'
                    ''')
                    has_character_name = cursor.fetchone()
                except Exception:
                    # Erro ao verificar coluna, fazer rollback
                    has_character_name = None
                    conn.rollback()
            
            if has_character_name:
                # Migrar tabela gearscore: remover character_name, adicionar constraint nova
                try:
                    # Dropar constraint antiga se existir
                    cursor.execute('''
                        ALTER TABLE gearscore 
                        DROP CONSTRAINT IF EXISTS gearscore_user_id_character_name_key
                    ''')
                    
                    # Adicionar coluna class_pvp se não existir (pode ter sido criada parcialmente)
                    cursor.execute('''
                        SELECT column_name 
                        FROM information_schema.columns 
                        WHERE table_name = 'gearscore' AND column_name = 'class_pvp'
                    ''')
                    if not cursor.fetchone():
                        cursor.execute('''
                            ALTER TABLE gearscore 
                            ADD COLUMN class_pvp TEXT
                        ''')
                        # Atualizar class_pvp com valor padrão se necessário
                        cursor.execute('''
                            UPDATE gearscore 
                            SET class_pvp = 'Unknown' 
                            WHERE class_pvp IS NULL
                        ''')
                        cursor.execute('''
                            ALTER TABLE gearscore 
                            ALTER COLUMN class_pvp SET NOT NULL
                        ''')
                    
                    # Remover coluna character_name
                    cursor.execute('''
                        ALTER TABLE gearscore 
                        DROP COLUMN IF EXISTS character_name
                    ''')
                    
                    # Adicionar constraint nova (se não existir)
                    try:
                        # Verificar se a constraint já existe
                        cursor.execute('''
                            SELECT constraint_name 
                            FROM information_schema.table_constraints 
                            WHERE table_name = 'gearscore' 
                            AND constraint_name = 'gearscore_user_id_class_pvp_key'
                        ''')
                        constraint_exists = cursor.fetchone()
                        
                        if not constraint_exists:
                            cursor.execute('''
                                ALTER TABLE gearscore 
                                ADD CONSTRAINT gearscore_user_id_class_pvp_key 
                                UNIQUE (user_id, class_pvp)
                            ''')
                    except Exception as e:
                        # Constraint já existe ou erro, fazer rollback e continuar
                        print(f"Aviso ao adicionar constraint: {e}")
                        conn.rollback()
                except Exception as e:
                    print(f"Aviso na migração de gearscore: {e}")
                    conn.rollback()
            
            # Adicionar coluna character_name se não existir
            try:
                cursor.execute('''
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'gearscore' AND column_name = 'character_name'
                ''')
                has_character_name = cursor.fetchone()
            except Exception:
                # Tabela pode não existir ainda
                has_character_name = None
                conn.rollback()
            
            if not has_character_name:
                try:
                    cursor.execute('''
                        ALTER TABLE gearscore 
                        ADD COLUMN character_name TEXT
                    ''')
                except Exception as e:
                    print(f"Aviso ao adicionar character_name: {e}")
                    conn.rollback()
        except Exception as e:
            print(f"Erro na inicialização: {e}")
            conn.rollback()
        
        # Criar tabela gearscore com estrutura nova
        try:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gearscore (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    family_name TEXT NOT NULL,
                    character_name TEXT,
                    class_pvp TEXT NOT NULL,
                    ap INTEGER NOT NULL,
                    aap INTEGER NOT NULL,
                    dp INTEGER NOT NULL,
                    linkgear TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, class_pvp)
                )
            ''')
        except Exception as e:
            print(f"Aviso ao criar tabela gearscore: {e}")
            conn.rollback()
        
        # Verificar se tabela gearscore_history existe
        try:
            cursor.execute('''
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'gearscore_history'
                )
            ''')
            history_table_exists = cursor.fetchone()[0]
        except Exception:
            history_table_exists = False
            conn.rollback()
        
        if history_table_exists:
            # Verificar estrutura da tabela existente
            cursor.execute('''
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'gearscore_history' AND column_name = 'character_name'
            ''')
            has_history_character_name = cursor.fetchone()
            
            cursor.execute('''
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_name = 'gearscore_history' AND column_name = 'class_pvp'
            ''')
            has_history_class_pvp = cursor.fetchone()
            
            if has_history_character_name and not has_history_class_pvp:
                # Migrar tabela gearscore_history: estrutura antiga
                try:
                    # Adicionar coluna class_pvp
                    cursor.execute('''
                        ALTER TABLE gearscore_history 
                        ADD COLUMN class_pvp TEXT DEFAULT 'Unknown'
                    ''')
                    cursor.execute('''
                        ALTER TABLE gearscore_history 
                        ALTER COLUMN class_pvp SET NOT NULL
                    ''')
                    cursor.execute('''
                        ALTER TABLE gearscore_history 
                        ALTER COLUMN class_pvp DROP DEFAULT
                    ''')
                    
                    # Remover coluna character_name
                    cursor.execute('''
                        ALTER TABLE gearscore_history 
                        DROP COLUMN IF EXISTS character_name
                    ''')
                    
                    # Dropar índice antigo se existir
                    try:
                        cursor.execute('''
                            DROP INDEX IF EXISTS idx_history_user_char
                        ''')
                    except:
                        pass
                except Exception as e:
                    print(f"Aviso na migração de gearscore_history: {e}")
        else:
            # Criar tabela gearscore_history com estrutura nova
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS gearscore_history (
                    id SERIAL PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    class_pvp TEXT NOT NULL,
                    ap INTEGER NOT NULL,
                    aap INTEGER NOT NULL,
                    dp INTEGER NOT NULL,
                    total_gs INTEGER NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        
        # Criar índice (só se a coluna class_pvp existir)
        try:
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_history_user_class 
                ON gearscore_history(user_id, class_pvp, created_at)
            ''')
        except Exception as e:
            print(f"Aviso ao criar índice: {e}")
            conn.rollback()
        
        # Tabela de eventos (GvG, Treino, etc)
        try:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS eventos (
                    id SERIAL PRIMARY KEY,
                    tipo TEXT NOT NULL,
                    nome TEXT NOT NULL,
                    canal_voz TEXT,
                    criado_por TEXT NOT NULL,
                    criado_por_nome TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    mes_referencia TEXT NOT NULL
                )
            ''')
        except Exception as e:
            print(f"Aviso ao criar tabela eventos: {e}")
            conn.rollback()
        
        # Tabela de participações em eventos
        try:
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS participacoes (
                    id SERIAL PRIMARY KEY,
                    evento_id INTEGER NOT NULL REFERENCES eventos(id),
                    user_id TEXT NOT NULL,
                    family_name TEXT,
                    display_name TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        except Exception as e:
            print(f"Aviso ao criar tabela participacoes: {e}")
            conn.rollback()
        
        # Índices para performance
        try:
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_eventos_mes 
                ON eventos(mes_referencia, tipo)
            ''')
            cursor.execute('''
                CREATE INDEX IF NOT EXISTS idx_participacoes_evento 
                ON participacoes(evento_id, user_id)
            ''')
        except Exception as e:
            print(f"Aviso ao criar índices de eventos: {e}")
            conn.rollback()
        
        try:
            conn.commit()
        except Exception as e:
            print(f"Erro ao fazer commit: {e}")
            conn.rollback()
        finally:
            cursor.close()
            conn.close()
    
    def register_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear, character_name=None):
        """Registra um novo gearscore (primeira vez)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Verificar se já existe registro para esta classe
        cursor.execute('''
            SELECT class_pvp FROM gearscore 
            WHERE user_id = %s AND class_pvp = %s
        ''', (user_id, class_pvp))
        existing = cursor.fetchone()
        
        if existing:
            conn.close()
            raise ValueError(f"Você já possui um registro para a classe {class_pvp}. Use /atualizar para modificar.")
        
        # Inserir novo registro
        cursor.execute('''
            INSERT INTO gearscore 
            (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
        ''', (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear))
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        try:
            cursor.execute('''
                INSERT INTO gearscore_history 
                (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ''', (user_id, class_pvp, ap, aap, dp, total_gs))
        except Exception as e:
            print(f"⚠️ Erro ao salvar histórico no register: {e}")
            # Não falhar o registro se o histórico falhar
        
        conn.commit()
        cursor.close()
        conn.close()
    
    def get_user_current_data(self, user_id):
        """Retorna os dados atuais do usuário (family_name, character_name, class_pvp)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT family_name, character_name, class_pvp FROM gearscore 
            WHERE user_id = %s
            LIMIT 1
        ''', (user_id,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    
    def update_gearscore(self, user_id, family_name=None, class_pvp=None, ap=None, aap=None, dp=None, linkgear=None, character_name=None):
        """Atualiza o gearscore de um personagem (pode mudar de classe)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Buscar dados atuais
        current_data = self.get_user_current_data(user_id)
        if not current_data:
            conn.close()
            raise ValueError("Você ainda não possui um registro! Use /registro primeiro.")
        
        current_family_name, current_character_name, current_class_pvp = current_data
        
        # Se não forneceu classe_pvp, usar a atual
        if class_pvp is None:
            class_pvp = current_class_pvp
        
        # Usar valores atuais se não fornecidos
        if family_name is None:
            family_name = current_family_name
        
        # character_name pode ser None se mudou de classe (personagem diferente)
        # Se não foi fornecido e não mudou de classe, manter o atual
        # Se mudou de classe e não forneceu, manter None (será limpo)
        if character_name is None:
            if class_pvp == current_class_pvp:
                # Não mudou de classe, manter o nome atual
                character_name = current_character_name
            # Se mudou de classe, character_name permanece None (será limpo)
        if ap is None or aap is None or dp is None or linkgear is None:
            # Buscar valores atuais de AP, AAP, DP e linkgear
            cursor.execute('''
                SELECT ap, aap, dp, linkgear FROM gearscore 
                WHERE user_id = %s
            ''', (user_id,))
            current_values = cursor.fetchone()
            if current_values:
                if ap is None:
                    ap = current_values[0]
                if aap is None:
                    aap = current_values[1]
                if dp is None:
                    dp = current_values[2]
                if linkgear is None:
                    linkgear = current_values[3]
        
        # Verificar se mudou de classe
        if class_pvp != current_class_pvp:
            # Remover registro da classe antiga
            cursor.execute('''
                DELETE FROM gearscore 
                WHERE user_id = %s AND class_pvp = %s
            ''', (user_id, current_class_pvp))
        
        # Atualizar ou inserir gearscore
        cursor.execute('''
            INSERT INTO gearscore 
            (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (user_id, class_pvp) 
            DO UPDATE SET 
                family_name = EXCLUDED.family_name,
                character_name = EXCLUDED.character_name,
                ap = EXCLUDED.ap,
                aap = EXCLUDED.aap,
                dp = EXCLUDED.dp,
                linkgear = EXCLUDED.linkgear,
                updated_at = CURRENT_TIMESTAMP
        ''', (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear))
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        try:
            cursor.execute('''
                INSERT INTO gearscore_history 
                (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ''', (user_id, class_pvp, ap, aap, dp, total_gs))
        except Exception as e:
            print(f"⚠️ Erro ao salvar histórico no update: {e}")
            # Não falhar a atualização se o histórico falhar
        
        conn.commit()
        cursor.close()
        conn.close()
    
    def get_gearscore(self, user_id, class_pvp=None):
        """Busca o gearscore de um usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Usar colunas explícitas para garantir ordem consistente
        if class_pvp:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id = %s AND class_pvp = %s
            ''', (user_id, class_pvp))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id = %s
                ORDER BY updated_at DESC
            ''', (user_id,))
        
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        return result
    
    def get_user_current_class(self, user_id):
        """Retorna a classe atual do usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT class_pvp FROM gearscore 
            WHERE user_id = %s
            LIMIT 1
        ''', (user_id,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result[0] if result else None
    
    def get_all_gearscores(self, valid_user_ids=None):
        """
        Busca todos os gearscores
        
        Args:
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if valid_user_ids:
            placeholders = ','.join(['%s'] * len(valid_user_ids))
            query = f'''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE user_id IN ({placeholders})
                ORDER BY updated_at DESC
            '''
            cursor.execute(query, list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                ORDER BY updated_at DESC
            ''')
        
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        return result
    
    def get_class_statistics(self, valid_user_ids=None):
        """
        Retorna estatísticas por classe
        
        Args:
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        if valid_user_ids:
            placeholders = ','.join(['%s'] * len(valid_user_ids))
            query = f'''
                SELECT 
                    class_pvp,
                    COUNT(*) as total,
                    AVG(GREATEST(ap, aap) + dp) as avg_gs,
                    AVG(ap) as avg_ap,
                    AVG(aap) as avg_aap,
                    AVG(dp) as avg_dp
                FROM gearscore
                WHERE user_id IN ({placeholders})
                GROUP BY class_pvp
                ORDER BY total DESC, avg_gs DESC
            '''
            cursor.execute(query, list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT 
                    class_pvp,
                    COUNT(*) as total,
                    AVG(GREATEST(ap, aap) + dp) as avg_gs,
                    AVG(ap) as avg_ap,
                    AVG(aap) as avg_aap,
                    AVG(dp) as avg_dp
                FROM gearscore
                GROUP BY class_pvp
                ORDER BY total DESC, avg_gs DESC
            ''')
        
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        return result
    
    def get_class_members(self, class_pvp, valid_user_ids=None):
        """
        Retorna todos os membros de uma classe específica
        
        Args:
            class_pvp: Nome da classe
            valid_user_ids: Set ou lista de user_ids válidos (que têm o cargo da guilda).
                           Se None, retorna todos os registros.
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # Usar colunas explícitas para garantir ordem consistente
        if valid_user_ids:
            placeholders = ','.join(['%s'] * len(valid_user_ids))
            query = f'''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE class_pvp = %s AND user_id IN ({placeholders})
                ORDER BY (GREATEST(ap, aap) + dp) DESC
            '''
            cursor.execute(query, [class_pvp] + list(valid_user_ids))
        else:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE class_pvp = %s
                ORDER BY (GREATEST(ap, aap) + dp) DESC
            ''', (class_pvp,))
        
        result = cursor.fetchall()
        cursor.close()
        conn.close()
        return result
    
    def get_user_history(self, user_id, class_pvp=None):
        """Retorna histórico de progressão de um usuário"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            if class_pvp:
                cursor.execute('''
                    SELECT ap, aap, dp, total_gs, created_at
                    FROM gearscore_history
                    WHERE user_id = %s AND class_pvp = %s
                    ORDER BY created_at DESC
                    LIMIT 50
                ''', (user_id, class_pvp))
            else:
                cursor.execute('''
                    SELECT class_pvp, ap, aap, dp, total_gs, created_at
                    FROM gearscore_history
                    WHERE user_id = %s
                    ORDER BY created_at DESC
                    LIMIT 50
                ''', (user_id,))
            
            result = cursor.fetchall()
        except Exception as e:
            print(f"⚠️ Erro ao buscar histórico: {e}")
            # Tentar verificar se a tabela existe e tem a estrutura correta
            try:
                cursor.execute('''
                    SELECT column_name 
                    FROM information_schema.columns 
                    WHERE table_name = 'gearscore_history'
                ''')
                columns = [row[0] for row in cursor.fetchall()]
                print(f"Colunas na tabela gearscore_history: {columns}")
            except:
                pass
            result = []
        finally:
            cursor.close()
            conn.close()
        
        return result
    
    def get_user_progress(self, user_id, class_pvp):
        """Calcula progressão de um personagem"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT 
                MIN(total_gs) as first_gs,
                MAX(total_gs) as current_gs,
                MAX(total_gs) - MIN(total_gs) as progress,
                COUNT(*) as updates,
                MIN(created_at) as first_update,
                MAX(created_at) as last_update
            FROM gearscore_history
            WHERE user_id = %s AND class_pvp = %s
        ''', (user_id, class_pvp))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    
    def clear_all_data(self):
        """Limpa todos os dados do banco (gearscore e histórico)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Limpar histórico primeiro (devido a foreign keys se houver)
            cursor.execute('DELETE FROM gearscore_history')
            # Limpar gearscore
            cursor.execute('DELETE FROM gearscore')
            
            conn.commit()
            
            cursor.close()
            conn.close()
            
            return True, "Todos os dados foram limpos com sucesso!"
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            return False, f"Erro ao limpar banco de dados: {str(e)}"
    
    def clear_history_only(self):
        """Limpa apenas o histórico, mantendo os gearscores atuais"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute('DELETE FROM gearscore_history')
            deleted_count = cursor.rowcount if hasattr(cursor, 'rowcount') else 0
            
            conn.commit()
            cursor.close()
            conn.close()
            
            return True, f"Histórico limpo! {deleted_count} registro(s) removido(s)."
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            return False, f"Erro ao limpar histórico: {str(e)}"
    
    def delete_user_gearscore(self, user_id):
        """Deleta o registro de gearscore de um usuário específico"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Verificar se o usuário tem registro
            cursor.execute('SELECT family_name, class_pvp FROM gearscore WHERE user_id = %s', (user_id,))
            result = cursor.fetchone()
            
            if not result:
                cursor.close()
                conn.close()
                return False, "Usuário não possui registro de gearscore."
            
            family_name, class_pvp = result
            
            # Deletar registro do gearscore
            cursor.execute('DELETE FROM gearscore WHERE user_id = %s', (user_id,))
            
            # Deletar histórico do usuário
            cursor.execute('DELETE FROM gearscore_history WHERE user_id = %s', (user_id,))
            
            conn.commit()
            cursor.close()
            conn.close()
            return True, f"Registro de {family_name} ({class_pvp}) excluído com sucesso!"
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            return False, f"Erro ao excluir registro: {str(e)}"
    
    def admin_update_gearscore(self, user_id, family_name=None, character_name=None, class_pvp=None, ap=None, aap=None, dp=None, linkgear=None):
        """Atualiza o gearscore de um usuário (admin - força atualização mesmo se não existir)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Buscar dados atuais
            cursor.execute('''
                SELECT family_name, character_name, class_pvp, ap, aap, dp, linkgear 
                FROM gearscore WHERE user_id = %s
            ''', (user_id,))
            current = cursor.fetchone()
            
            if not current:
                cursor.close()
                conn.close()
                return False, "Usuário não possui registro de gearscore. Use /registro_manual primeiro."
            
            # Usar valores atuais se não fornecidos
            current_family_name, current_character_name, current_class_pvp, current_ap, current_aap, current_dp, current_linkgear = current
            
            family_name = family_name if family_name is not None else current_family_name
            character_name = character_name if character_name is not None else current_character_name
            class_pvp = class_pvp if class_pvp is not None else current_class_pvp
            ap = ap if ap is not None else current_ap
            aap = aap if aap is not None else current_aap
            dp = dp if dp is not None else current_dp
            linkgear = linkgear if linkgear is not None else current_linkgear
            
            # Se mudou de classe, precisamos atualizar o registro corretamente
            if class_pvp != current_class_pvp:
                # Remover registro da classe antiga
                cursor.execute('DELETE FROM gearscore WHERE user_id = %s', (user_id,))
            
            # Atualizar ou inserir gearscore
            cursor.execute('''
                INSERT INTO gearscore 
                (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id, class_pvp) 
                DO UPDATE SET 
                    family_name = EXCLUDED.family_name,
                    character_name = EXCLUDED.character_name,
                    ap = EXCLUDED.ap,
                    aap = EXCLUDED.aap,
                    dp = EXCLUDED.dp,
                    linkgear = EXCLUDED.linkgear,
                    updated_at = CURRENT_TIMESTAMP
            ''', (user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear))
            
            # Salvar histórico
            total_gs = max(ap, aap) + dp
            cursor.execute('''
                INSERT INTO gearscore_history 
                (user_id, class_pvp, ap, aap, dp, total_gs, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ''', (user_id, class_pvp, ap, aap, dp, total_gs))
            
            conn.commit()
            cursor.close()
            conn.close()
            return True, f"Registro de {family_name} atualizado com sucesso! GS: {total_gs}"
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            return False, f"Erro ao atualizar registro: {str(e)}"
    
    def get_gearscore_by_family_name(self, family_name):
        """Busca o gearscore de um usuário pelo nome de família (case-insensitive)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
            FROM gearscore 
            WHERE LOWER(family_name) = LOWER(%s)
        ''', (family_name,))
        
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result
    
    def get_gearscores_by_family_names(self, family_names):
        """Busca o gearscore de múltiplos usuários pelos nomes de família"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        results = {}
        for name in family_names:
            cursor.execute('''
                SELECT id, user_id, family_name, character_name, class_pvp, ap, aap, dp, linkgear, updated_at
                FROM gearscore 
                WHERE LOWER(family_name) = LOWER(%s)
            ''', (name.strip(),))
            
            result = cursor.fetchone()
            if result:
                results[name.strip().lower()] = result
        
        cursor.close()
        conn.close()
        return results
    
    # ============================================
    # MÉTODOS PARA EVENTOS E PARTICIPAÇÕES
    # ============================================
    
    def registrar_evento(self, tipo, nome, canal_voz, criado_por, criado_por_nome, participantes):
        """
        Registra um evento e suas participações.
        participantes: lista de dicts com {user_id, family_name, display_name}
        Retorna: (evento_id, quantidade_participantes)
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        try:
            # Determinar mês de referência (formato: YYYY-MM)
            from datetime import datetime
            mes_referencia = datetime.now().strftime("%Y-%m")
            
            # Inserir evento
            cursor.execute('''
                INSERT INTO eventos (tipo, nome, canal_voz, criado_por, criado_por_nome, mes_referencia)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
            ''', (tipo, nome, canal_voz, criado_por, criado_por_nome, mes_referencia))
            
            evento_id = cursor.fetchone()[0]
            
            # Inserir participações
            for p in participantes:
                cursor.execute('''
                    INSERT INTO participacoes (evento_id, user_id, family_name, display_name)
                    VALUES (%s, %s, %s, %s)
                ''', (evento_id, p['user_id'], p.get('family_name'), p.get('display_name')))
            
            conn.commit()
            cursor.close()
            conn.close()
            return evento_id, len(participantes)
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            raise e
    
    def get_relatorio_participacoes(self, mes_referencia=None):
        """
        Retorna relatório de participações do mês.
        Se mes_referencia for None, usa o mês atual.
        Retorna: {
            'eventos_por_tipo': {tipo: quantidade},
            'participacoes_por_player': {user_id: {tipo: quantidade, 'display_name': nome}},
            'total_eventos': int,
            'mes': str
        }
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        from datetime import datetime
        if mes_referencia is None:
            mes_referencia = datetime.now().strftime("%Y-%m")
        
        # Contar eventos por tipo
        cursor.execute('''
            SELECT tipo, COUNT(*) as total
            FROM eventos
            WHERE mes_referencia = %s
            GROUP BY tipo
        ''', (mes_referencia,))
        
        eventos_por_tipo = {}
        for row in cursor.fetchall():
            eventos_por_tipo[row[0]] = row[1]
        
        total_eventos = sum(eventos_por_tipo.values())
        
        # Contar participações por player e tipo
        cursor.execute('''
            SELECT p.user_id, p.display_name, p.family_name, e.tipo, COUNT(*) as total
            FROM participacoes p
            JOIN eventos e ON p.evento_id = e.id
            WHERE e.mes_referencia = %s
            GROUP BY p.user_id, p.display_name, p.family_name, e.tipo
        ''', (mes_referencia,))
        
        participacoes_por_player = {}
        for row in cursor.fetchall():
            user_id = row[0]
            display_name = row[1]
            family_name = row[2]
            tipo = row[3]
            quantidade = row[4]
            
            if user_id not in participacoes_por_player:
                participacoes_por_player[user_id] = {
                    'display_name': display_name or family_name or user_id,
                    'family_name': family_name
                }
            participacoes_por_player[user_id][tipo] = quantidade
        
        cursor.close()
        conn.close()
        
        return {
            'eventos_por_tipo': eventos_por_tipo,
            'participacoes_por_player': participacoes_por_player,
            'total_eventos': total_eventos,
            'mes': mes_referencia
        }
    
    def limpar_eventos_mes_anterior(self):
        """Limpa eventos de meses anteriores ao atual (chamado no dia 1)"""
        conn = self.get_connection()
        cursor = conn.cursor()
        
        from datetime import datetime
        mes_atual = datetime.now().strftime("%Y-%m")
        
        try:
            # Deletar participações de eventos antigos
            cursor.execute('''
                DELETE FROM participacoes 
                WHERE evento_id IN (
                    SELECT id FROM eventos WHERE mes_referencia < %s
                )
            ''', (mes_atual,))
            
            # Deletar eventos antigos
            cursor.execute('''
                DELETE FROM eventos WHERE mes_referencia < %s
            ''', (mes_atual,))
            
            deleted = cursor.rowcount
            conn.commit()
            cursor.close()
            conn.close()
            return deleted
        except Exception as e:
            conn.rollback()
            cursor.close()
            conn.close()
            raise e

