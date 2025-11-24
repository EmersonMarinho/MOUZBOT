"""
Versão do banco de dados usando MongoDB Atlas
Instale: pip install pymongo
"""
from pymongo import MongoClient
from datetime import datetime
from config import MONGODB_URI, MONGODB_DB_NAME

class Database:
    def __init__(self):
        self.client = MongoClient(MONGODB_URI)
        self.db = self.client[MONGODB_DB_NAME]
        self.collection = self.db['gearscore']
        self.history_collection = self.db['gearscore_history']
        self.init_database()
    
    def init_database(self):
        """Inicializa o banco de dados criando índices"""
        # Criar índice único para user_id + class_pvp
        self.collection.create_index(
            [("user_id", 1), ("class_pvp", 1)],
            unique=True
        )
        # Índice para histórico
        self.history_collection.create_index(
            [("user_id", 1), ("class_pvp", 1), ("created_at", -1)]
        )
    
    def register_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear):
        """Registra um novo gearscore (primeira vez)"""
        # Verificar se já existe registro para esta classe
        existing = self.collection.find_one({
            "user_id": user_id,
            "class_pvp": class_pvp
        })
        
        if existing:
            raise ValueError(f"Você já possui um registro para a classe {class_pvp}. Use /atualizar para modificar.")
        
        # Inserir novo registro
        document = {
            "user_id": user_id,
            "family_name": family_name,
            "class_pvp": class_pvp,
            "ap": ap,
            "aap": aap,
            "dp": dp,
            "linkgear": linkgear,
            "updated_at": datetime.utcnow()
        }
        
        self.collection.insert_one(document)
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        history_doc = {
            "user_id": user_id,
            "class_pvp": class_pvp,
            "ap": ap,
            "aap": aap,
            "dp": dp,
            "total_gs": total_gs,
            "created_at": datetime.utcnow()
        }
        self.history_collection.insert_one(history_doc)
    
    def update_gearscore(self, user_id, family_name, class_pvp, ap, aap, dp, linkgear):
        """Atualiza o gearscore de um personagem (pode mudar de classe)"""
        # Verificar se já existe registro para esta classe
        existing_same_class = self.collection.find_one({
            "user_id": user_id,
            "class_pvp": class_pvp
        })
        
        # Se mudou de classe, remover registro da classe antiga
        if not existing_same_class:
            # Buscar classe antiga
            old_record = self.collection.find_one({"user_id": user_id})
            if old_record and old_record.get("class_pvp") != class_pvp:
                # Remover registro da classe antiga
                self.collection.delete_one({
                    "user_id": user_id,
                    "class_pvp": old_record["class_pvp"]
                })
        
        # Atualizar ou inserir gearscore
        document = {
            "user_id": user_id,
            "family_name": family_name,
            "class_pvp": class_pvp,
            "ap": ap,
            "aap": aap,
            "dp": dp,
            "linkgear": linkgear,
            "updated_at": datetime.utcnow()
        }
        
        self.collection.update_one(
            {"user_id": user_id, "class_pvp": class_pvp},
            {"$set": document},
            upsert=True
        )
        
        # Salvar histórico
        total_gs = max(ap, aap) + dp
        history_doc = {
            "user_id": user_id,
            "class_pvp": class_pvp,
            "ap": ap,
            "aap": aap,
            "dp": dp,
            "total_gs": total_gs,
            "created_at": datetime.utcnow()
        }
        self.history_collection.insert_one(history_doc)
    
    def get_gearscore(self, user_id, class_pvp=None):
        """Busca o gearscore de um usuário"""
        if class_pvp:
            result = self.collection.find_one({
                "user_id": user_id,
                "class_pvp": class_pvp
            })
            return [result] if result else []
        else:
            results = list(self.collection.find(
                {"user_id": user_id}
            ).sort("updated_at", -1))
            return results
    
    def get_user_current_class(self, user_id):
        """Retorna a classe atual do usuário"""
        result = self.collection.find_one({"user_id": user_id})
        return result.get("class_pvp") if result else None
    
    def get_all_gearscores(self):
        """Busca todos os gearscores"""
        results = list(self.collection.find().sort("updated_at", -1))
        return results
    
    def get_class_statistics(self):
        """Retorna estatísticas por classe"""
        pipeline = [
            {
                "$addFields": {
                    "gs": {
                        "$add": [
                            {"$max": ["$ap", "$aap"]},
                            "$dp"
                        ]
                    }
                }
            },
            {
                "$group": {
                    "_id": "$class_pvp",
                    "total": {"$sum": 1},
                    "avg_gs": {"$avg": "$gs"},
                    "avg_ap": {"$avg": "$ap"},
                    "avg_aap": {"$avg": "$aap"},
                    "avg_dp": {"$avg": "$dp"}
                }
            },
            {
                "$project": {
                    "class_pvp": "$_id",
                    "total": 1,
                    "avg_gs": 1,
                    "avg_ap": 1,
                    "avg_aap": 1,
                    "avg_dp": 1,
                    "_id": 0
                }
            },
            {"$sort": {"total": -1, "avg_gs": -1}}
        ]
        results = list(self.collection.aggregate(pipeline))
        return results
    
    def get_class_members(self, class_pvp):
        """Retorna todos os membros de uma classe específica"""
        # Ordenar por GS (MAX(AP, AAP) + DP)
        pipeline = [
            {"$match": {"class_pvp": class_pvp}},
            {
                "$addFields": {
                    "gs": {
                        "$add": [
                            {"$max": ["$ap", "$aap"]},
                            "$dp"
                        ]
                    }
                }
            },
            {"$sort": {"gs": -1}}
        ]
        results = list(self.collection.aggregate(pipeline))
        # Remover campo calculado antes de retornar
        for result in results:
            result.pop('gs', None)
        return results
    
    def get_user_history(self, user_id, class_pvp=None):
        """Retorna histórico de progressão de um usuário"""
        query = {"user_id": user_id}
        if class_pvp:
            query["class_pvp"] = class_pvp
        
        results = list(self.history_collection.find(query).sort("created_at", -1).limit(50))
        return results
    
    def get_user_progress(self, user_id, class_pvp):
        """Calcula progressão de um personagem"""
        pipeline = [
            {"$match": {"user_id": user_id, "class_pvp": class_pvp}},
            {
                "$group": {
                    "_id": None,
                    "first_gs": {"$min": "$total_gs"},
                    "current_gs": {"$max": "$total_gs"},
                    "updates": {"$sum": 1},
                    "first_update": {"$min": "$created_at"},
                    "last_update": {"$max": "$created_at"}
                }
            },
            {
                "$project": {
                    "first_gs": 1,
                    "current_gs": 1,
                    "progress": {"$subtract": ["$current_gs", "$first_gs"]},
                    "updates": 1,
                    "first_update": 1,
                    "last_update": 1,
                    "_id": 0
                }
            }
        ]
        result = list(self.history_collection.aggregate(pipeline))
        return result[0] if result else None
    
    def get_user_history(self, user_id, character_name=None):
        """Retorna histórico de progressão de um usuário"""
        query = {"user_id": user_id}
        if character_name:
            query["character_name"] = character_name
        
        results = list(self.history_collection.find(query).sort("created_at", -1).limit(50))
        return results
    
    def get_user_progress(self, user_id, character_name):
        """Calcula progressão de um personagem"""
        pipeline = [
            {"$match": {"user_id": user_id, "character_name": character_name}},
            {
                "$group": {
                    "_id": None,
                    "first_gs": {"$min": "$total_gs"},
                    "current_gs": {"$max": "$total_gs"},
                    "updates": {"$sum": 1},
                    "first_update": {"$min": "$created_at"},
                    "last_update": {"$max": "$created_at"}
                }
            },
            {
                "$project": {
                    "first_gs": 1,
                    "current_gs": 1,
                    "progress": {"$subtract": ["$current_gs", "$first_gs"]},
                    "updates": 1,
                    "first_update": 1,
                    "last_update": 1,
                    "_id": 0
                }
            }
        ]
        result = list(self.history_collection.aggregate(pipeline))
        return result[0] if result else None
    
    def clear_all_data(self):
        """Limpa todos os dados do banco (gearscore e histórico)"""
        try:
            # Limpar histórico
            self.history_collection.delete_many({})
            # Limpar gearscore
            self.collection.delete_many({})
            return True, "Todos os dados foram limpos com sucesso!"
        except Exception as e:
            return False, f"Erro ao limpar banco de dados: {str(e)}"
    
    def clear_history_only(self):
        """Limpa apenas o histórico, mantendo os gearscores atuais"""
        try:
            result = self.history_collection.delete_many({})
            deleted_count = result.deleted_count
            return True, f"Histórico limpo! {deleted_count} registro(s) removido(s)."
        except Exception as e:
            return False, f"Erro ao limpar histórico: {str(e)}"
    
    def close(self):
        """Fecha a conexão com o banco de dados"""
        self.client.close()

