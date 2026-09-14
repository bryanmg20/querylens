from config.connections import get_connection_postgres, get_connection_mysql, get_connection_querylens_db # Import database connection functions
from collectors.factory import Engine_Factory # Import the factory module for collector creation

from sqlalchemy import text  # Import the text function from SQLAlchemy for executing raw SQL queries
import json

def main():

    postgres_engine = get_connection_postgres()  # Get PostgreSQL engine
    mysql_engine = get_connection_mysql()  # Get MySQL engine
    creator = Engine_Factory()  # Create PostgreSQL collector

    collector_postgres = creator.create_collector('postgres', postgres_engine)  # Create the collector instance
    collector_postgres.collect_telemetry()  # Collect telemetry data from PostgreSQL
  

    collector_mysql = creator.create_collector('mysql', mysql_engine)  # Create the collector instance
    collector_mysql.collect_telemetry()  # Collect telemetry data from MySQL

    querylens_engine = get_connection_querylens_db()  # Get QueryLens engine
    payload = collector_postgres.get_stats()  # Get the collected telemetry data from PostgreSQL or Mysql
    payload["source"] = "postgres"  # Add source information to the payload, you have to change this line to "mysql" if you want to send MySQL data instead

    payload_json = json.dumps(payload, default=str)  # Convert the payload to JSON format

    with querylens_engine.begin() as conn:
        query = text("SELECT * FROM pgmq.send(:queue_name, CAST(:payload AS JSONB)) AS msg_id;")  # Prepare the SQL query to send the payload to the queue
        result = conn.execute(
            query, 
            {"queue_name": "analyze_job", "payload": payload_json}
        )
        mensaje = result.mappings().first()
        print(f"Diccionario encolado con ID: {mensaje['msg_id']}")
  
    
if __name__ == "__main__":
    main()