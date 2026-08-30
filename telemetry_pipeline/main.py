from databases import get_connection_postgres, get_connection_mysql  # Import database connection functions
from collectors.factory import Engine_Factory # Import the factory module for collector creation

def main():

    postgres_engine = get_connection_postgres()  # Get PostgreSQL engine
    mysql_engine = get_connection_mysql()  # Get MySQL engine
    creator = Engine_Factory()  # Create PostgreSQL collector

    collector_postgres = creator.create_collector('postgres', postgres_engine)  # Create the collector instance
    collector_postgres.collect_telemetry()  # Collect telemetry data from PostgreSQL
    print(collector_postgres.get_candidates())  # Get the collected telemetry data
    #print(collector_postgres.get_active_queries())  # Get the collected statistics
    #print(collector_postgres.get_query_explain())  # Get the collected statistics
    collector_mysql = creator.create_collector('mysql', mysql_engine)  # Create the collector instance
    collector_mysql.collect_telemetry()  # Collect telemetry data from MySQL
    print(collector_mysql.get_stats_complete())  # Get the collected telemetry data
    
if __name__ == "__main__":
    main()