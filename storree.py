import libtorrent as lt
import opendht
import time
import platform
import os
import argparse
import sqlite3

def create_torrent(file_path):
    fs = lt.file_storage()
    lt.add_files(fs, file_path)
    t = lt.create_torrent(fs)
    parent_path = os.path.abspath(os.path.dirname(file_path))
    lt.set_piece_hashes(t, parent_path)
    torrent = t.generate()
    torrent_data = lt.bencode(torrent)
    info = lt.torrent_info(lt.bdecode(torrent_data))
    return info


def start_session():
    return lt.session({'listen_interfaces': '0.0.0.0:6881', 'enable_dht': True})


def add_torrent_to_session(ses, magnet_uri, save_path):
    p = lt.parse_magnet_uri(magnet_uri)
    p.save_path = save_path
    return ses.add_torrent(p)
    # return ses.add_torrent({'url': magnet_uri, 'save_path': save_path})


def publish_to_dht(dht_node, user_id, file_name, magnet_uri):
    data_to_publish = f"{file_name}::{magnet_uri}"
    dht_node.put(opendht.InfoHash.get(user_id),
                 opendht.Value(data_to_publish.encode()))
 

def print_transfer_status(handle, mode):
    if not handle.is_valid():
        return  # Skip if the handle is not valid
    status = handle.status()
    clear_terminal()
    print(f'{mode} {status.progress * 100:.2f}%, Peers: {status.num_peers}, '
          f'Download: {status.download_rate / 1000:.2f} kB/s, Upload: {status.upload_rate / 1000:.2f} kB/s, '
          f'Total Downloaded: {status.total_done / 1000000:.2f} MB, Total Uploaded: {status.total_upload / 1000000:.2f} MB')


def initialize_database():
    conn = sqlite3.connect('backup_status.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS kept
                 (magnet_link TEXT, user TEXT, path TEXT, filename TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS saved
                 (user TEXT, path TEXT, filename TEXT)''')
    conn.commit()
    return conn


def mirror(user_id, filename, path):
    dht_node = opendht.DhtRunner()
    bootstrap_dht_node(dht_node, 4222)  # Adjust port as needed

    files_info = lookup_dht(dht_node, user_id)
    conn = initialize_database()
    c = conn.cursor()

    for file_info in files_info:
        file_name, magnet_uri = file_info
        if file_name == filename:
            c.execute("INSERT INTO kept (magnet_link, user, path, filename) VALUES (?, ?, ?, ?)",
                      (magnet_uri, user_id, path, file_name))
            print(f"Mirrored {file_name}: {magnet_uri}")
            break
    else:
        print(f"No file named '{filename}' found for user ID '{user_id}'")

    conn.commit()
    conn.close()


def store_files(dht_node):
    ses = start_session()
    conn = initialize_database()
    print("Initialized DB")
    c = conn.cursor()
    c.execute("SELECT user, path FROM saved")
    for user, file_path in c.fetchall():
        print("Processing:", user, file_path)
        # Using the file's directory as the save path
        info = create_torrent(file_path)
        h = ses.add_torrent({'ti': info, 'save_path': os.path.dirname(file_path)})
        magnet_uri = lt.make_magnet_uri(h)
        save_path = os.path.dirname(file_path)
        handle = add_torrent_to_session(ses, magnet_uri, save_path)
        # Publish to DHT
        publish_to_dht(dht_node, user, os.path.basename(file_path), magnet_uri)
        print(f"Storing {file_path}: {magnet_uri}")
    try:
        while True:
            # Iterate over the handles and print their status
            for torrent in ses.get_torrents():
                print_transfer_status(torrent, "Seeding")
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nStoring stopped by user.")
    conn.close()


def new_files(directory, user_id):
    conn = initialize_database()
    c = conn.cursor()

    for file in os.listdir(directory):
        file_path = os.path.join(directory, file)
        if os.path.isfile(file_path):
            ses = lt.session(
                {'listen_interfaces': '0.0.0.0:6881', 'enable_dht': True})
            
            file_name = os.path.basename(file_path)

            # Insert into the 'saved' table
            c.execute("INSERT INTO saved (user, path, filename) VALUES (?, ?, ?)",
                      (user_id, file_path, file_name))

            print(f"Added {file_name}: {file_path}")

    conn.commit()
    conn.close()


def clear_terminal():
    pass
    # Clear the terminal screen.
    #os.system('cls' if platform.system() == 'Windows' else 'clear')


def initialize_download_session():
    conn = initialize_database()
    print("Initialized database")
    ses = start_session()
    print("Initialized torrent session")
    return conn, ses


def fetch_download_data(conn):
    c = conn.cursor()
    c.execute("SELECT magnet_link, path, filename FROM kept")
    return c.fetchall()


def clean_saved_files():
    conn = initialize_database()
    c = conn.cursor()
    c.execute("delete FROM saved;")
    conn.commit()
    conn.close()


def clean_kept_files():
    conn = initialize_database()
    c = conn.cursor()
    c.execute("delete FROM kept;")
    conn.commit()
    conn.close()


def start_downloads(ses, download_data):
    download_handles = []
    for magnet_link, download_path, filename in download_data:
        print("Downloading magnet:", magnet_link)
        handle = add_torrent_to_session(ses, magnet_link, download_path)
        download_handles.append((handle, filename))
    return download_handles


def print_download_status(download_handles):
    all_completed = False
    while not all_completed:
        all_completed = True
        clear_terminal()
        print("Current Download Status:\n")
        for handle, filename in download_handles:
            if not handle.is_valid():
                continue
            print_transfer_status(handle, f"Downloading {filename}")
            if not handle.status().is_seeding:
                all_completed = False
        time.sleep(1)
    return download_handles


def finalize_downloads(download_handles):
    for handle, filename in download_handles:
        print(f"\nDownload of '{filename}' complete. Continuing to seed...")
        continue_seeding(handle, filename)


def download_files():
    conn, ses = initialize_download_session()
    download_data = fetch_download_data(conn)
    download_handles = start_downloads(ses, download_data)

    try:
        download_handles = print_download_status(download_handles)
        finalize_downloads(download_handles)
    except KeyboardInterrupt:
        print("\nDownloading interrupted by user.")
    conn.close()


def lookup_dht(node, user_id):
    found = node.get(opendht.InfoHash.get(user_id))
    if not found:
        print(f"No entries found for user ID '{user_id}'")
        return None
    return [value.data.decode().split("::") for value in found]


def continue_seeding(handle, file_name):
    try:
        while True:
            print_transfer_status(handle, "Seeding")
            time.sleep(5)
    except KeyboardInterrupt:
        print(f"\nSeeding of '{file_name}' stopped by user.")


def bootstrap_dht_node(dht_node, port):
    dht_node.run(port=port)
    dht_node.bootstrap("bootstrap.jami.net", "4222")


def parse_arguments():
    parser = argparse.ArgumentParser(description="Decentralized Backup System")
    parser.add_argument('-p', '--port', type=int,
                        default=4222, help='Port for DHT node')
    subparsers = parser.add_subparsers(dest='command', required=True)

    parser_new = subparsers.add_parser('new')
    parser_new.add_argument('directory', type=str,
                            help='Directory containing files to add')
    parser_new.add_argument('user_id', type=str, help='Your user ID')

    subparsers.add_parser('store')

    parser_lookup = subparsers.add_parser('lookup')
    parser_lookup.add_argument('user_id', type=str, help='User ID to lookup')

    subparsers.add_parser('download')

    clean_parser = subparsers.add_parser('cleanup')
    clean_files_parser = clean_parser.add_subparsers(dest='cleanup_files', required=True)
    clean_files_parser.add_parser('kept')
    clean_files_parser.add_parser('saved')
    clean_files_parser.add_parser('all')

    parser_mirror = subparsers.add_parser('mirror')
    parser_mirror.add_argument(
        'user_id', type=str, help='User ID of the file owner')
    parser_mirror.add_argument('filename', type=str, help='Filename to mirror')
    parser_mirror.add_argument(
        'path', type=str, help='Path to store the mirrored file')

    return parser.parse_args()


def handle_lookup_command(args, dht_node):
    files_info = lookup_dht(dht_node, args.user_id)
    if files_info:
        for file_name, magnet_uri in files_info:
            print(f"{file_name}: {magnet_uri}")
    else:
        print(f"No files found for user ID '{args.user_id}'")


def main():
    args = parse_arguments()
    if args.command == 'cleanup':
        if args.cleanup_files == 'kept':
            clean_kept_files()
        elif args.cleanup_files == 'saved':
            clean_saved_files()
        elif args.cleanup_files == 'all':
            clean_saved_files()
            clean_kept_files()
    if args.command == 'new':
        new_files(args.directory, args.user_id)
    elif args.command == 'store':
        dht_node = opendht.DhtRunner()
        bootstrap_dht_node(dht_node, args.port)
        store_files(dht_node)
    elif args.command == 'lookup':
        dht_node = opendht.DhtRunner()
        bootstrap_dht_node(dht_node, args.port)
        handle_lookup_command(args, dht_node)
    elif args.command == 'download':
        download_files()
    elif args.command == 'mirror':
        mirror(args.user_id, args.filename, args.path)


if __name__ == "__main__":
    main()
