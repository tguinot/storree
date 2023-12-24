import libtorrent as lt
import os

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
