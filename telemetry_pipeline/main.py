from databases import get_connection_postgres, get_connection_mysql  # Import database connection functions
from collectors.factory import Engine_Factory # Import the factory module for collector creation

def main():

    postgres_engine = get_connection_postgres()  # Get PostgreSQL engine
    mysql_engine = get_connection_mysql()  # Get MySQL engine
    creator = Engine_Factory()  # Create PostgreSQL collector

    collector_postgres = creator.create_collector('postgres', postgres_engine)  # Create the collector instance
    collector_postgres.collect_telemetry()  # Collect telemetry data from PostgreSQL
  
    collector_postgres.get_canonic_explains()
    collector_postgres.get_candidates()
    collector_postgres.get_stats_complete()
    collector_postgres.get_locks()
    collector_postgres.get_active_queries()


    collector_mysql = creator.create_collector('mysql', mysql_engine)  # Create the collector instance
    collector_mysql.collect_telemetry()  # Collect telemetry data from MySQL
  
    collector_mysql.get_canonic_explains()  # Get the collected statistics
    collector_mysql.get_candidates()  # Get the collected statistics
    collector_mysql.get_statements()  # Get the collected statistics
    collector_mysql.get_locks()
    collector_mysql.get_active_queries()
    
if __name__ == "__main__":
    main()