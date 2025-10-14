use pyo3::prelude::*;
use pyo3::types::PyDict;
use imessage_database::{
    tables::{
        messages::Message,
        table::Table,
    },
    util::dirs::default_db_path,
};
use rusqlite::{Connection, OpenFlags, OptionalExtension};
use std::path::PathBuf;
use serde::{Serialize, Deserialize};

/// Python-accessible message structure
#[pyclass]
#[derive(Debug, Clone, Serialize, Deserialize)]
struct PyMessage {
    #[pyo3(get)]
    rowid: i32,
    #[pyo3(get)]
    guid: String,
    #[pyo3(get)]
    text: Option<String>,
    #[pyo3(get)]
    service: String,
    #[pyo3(get)]
    handle_id: Option<i32>,
    #[pyo3(get)]
    subject: Option<String>,
    #[pyo3(get)]
    date: f64,  // Unix timestamp
    #[pyo3(get)]
    date_read: Option<f64>,
    #[pyo3(get)]
    date_delivered: Option<f64>,
    #[pyo3(get)]
    is_from_me: bool,
    #[pyo3(get)]
    is_read: bool,
    #[pyo3(get)]
    is_sent: bool,
    #[pyo3(get)]
    is_delivered: bool,
    #[pyo3(get)]
    cache_roomnames: Option<String>,
    #[pyo3(get)]
    group_title: Option<String>,
    #[pyo3(get)]
    associated_message_guid: Option<String>,
    #[pyo3(get)]
    associated_message_type: Option<i32>,
    #[pyo3(get)]
    thread_originator_guid: Option<String>,
}

/// Python-accessible handle (contact) structure
#[pyclass]
#[derive(Debug, Clone, Serialize, Deserialize)]
struct PyHandle {
    #[pyo3(get)]
    rowid: i32,
    #[pyo3(get)]
    id: String,
    #[pyo3(get)]
    service: Option<String>,
    #[pyo3(get)]
    uncanonicalized_id: Option<String>,
}

/// Python-accessible attachment structure
#[pyclass]
#[derive(Debug, Clone, Serialize, Deserialize)]
struct PyAttachment {
    #[pyo3(get)]
    rowid: i32,
    #[pyo3(get)]
    guid: String,
    #[pyo3(get)]
    filename: Option<String>,
    #[pyo3(get)]
    mime_type: Option<String>,
    #[pyo3(get)]
    transfer_name: Option<String>,
    #[pyo3(get)]
    total_bytes: Option<i64>,
}

/// Main database interface
#[pyclass(unsendable)]
struct IMessageDB {
    conn: Connection,
    db_path: PathBuf,
}

#[pymethods]
impl IMessageDB {
    /// Create a new connection to the iMessage database
    #[new]
    fn new(db_path: Option<String>) -> PyResult<Self> {
        let db_path = match db_path {
            Some(path) => PathBuf::from(path),
            None => {
                let path = default_db_path();
                if path.exists() {
                    path
                } else {
                    return Err(PyErr::new::<pyo3::exceptions::PyIOError, _>(
                        "Could not find default iMessage database path"
                    ));
                }
            }
        };

        let conn = Connection::open_with_flags(
            &db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY
        ).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(
                format!("Failed to open database: {}", e)
            )
        })?;

        Ok(IMessageDB { conn, db_path })
    }

    /// Get the database path
    #[getter]
    fn path(&self) -> String {
        self.db_path.to_string_lossy().to_string()
    }

    /// Query messages after a specific timestamp
    fn query_messages_after(&self, timestamp: f64, limit: Option<usize>) -> PyResult<Vec<PyMessage>> {
        // Convert Unix timestamp to Apple's Core Data timestamp (seconds since 2001-01-01)
        let apple_timestamp = timestamp - 978307200.0;
        
        let query = if let Some(limit) = limit {
            format!(
                "SELECT 
                    m.*,
                    c.chat_id,
                    (SELECT COUNT(*) FROM message_attachment_join a WHERE m.ROWID = a.message_id) as num_attachments,
                    NULL as deleted_from,
                    0 as num_replies
                FROM message as m
                LEFT JOIN chat_message_join as c ON m.ROWID = c.message_id
                WHERE m.date > {} 
                ORDER BY m.date ASC 
                LIMIT {}",
                apple_timestamp as i64 * 1_000_000_000,  // Convert to nanoseconds
                limit
            )
        } else {
            format!(
                "SELECT 
                    m.*,
                    c.chat_id,
                    (SELECT COUNT(*) FROM message_attachment_join a WHERE m.ROWID = a.message_id) as num_attachments,
                    NULL as deleted_from,
                    0 as num_replies
                FROM message as m
                LEFT JOIN chat_message_join as c ON m.ROWID = c.message_id
                WHERE m.date > {} 
                ORDER BY m.date ASC",
                apple_timestamp as i64 * 1_000_000_000
            )
        };

        let mut stmt = self.conn.prepare(&query).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to prepare query: {}", e)
            )
        })?;

        let mut messages = Vec::new();
        let mut rows = stmt.query([]).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to execute query: {}", e)
            )
        })?;

        // We need a separate connection for generate_text
        let text_conn = Connection::open_with_flags(
            &self.db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY
        ).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(
                format!("Failed to open database for text extraction: {}", e)
            )
        })?;

        while let Some(row) = rows.next().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to fetch row: {}", e)
            )
        })? {
            // Create Message from row
            let mut msg = Message::from_row(row).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("Failed to parse message: {}", e)
                )
            })?;

            // Try to generate text from attributedBody if text is None
            let message_text = if msg.text.is_none() || msg.text.as_ref().map(|s| s.is_empty()).unwrap_or(false) {
                // Try to generate text from attributedBody
                match msg.generate_text(&text_conn) {
                    Ok(text) => Some(text.to_string()),
                    Err(_) => msg.text.clone()
                }
            } else {
                msg.text.clone()
            };

            // Convert to PyMessage
            let py_msg = PyMessage {
                rowid: msg.rowid,
                guid: msg.guid,
                text: message_text,
                service: msg.service.unwrap_or_else(|| "iMessage".to_string()),
                handle_id: msg.handle_id,
                subject: msg.subject,
                date: (msg.date as f64 / 1_000_000_000.0) + 978307200.0,
                date_read: if msg.date_read != 0 {
                    Some((msg.date_read as f64 / 1_000_000_000.0) + 978307200.0)
                } else {
                    None
                },
                date_delivered: if msg.date_delivered != 0 {
                    Some((msg.date_delivered as f64 / 1_000_000_000.0) + 978307200.0)
                } else {
                    None
                },
                is_from_me: msg.is_from_me,
                is_read: msg.is_read,
                is_sent: true,  // Messages in the database are always sent
                is_delivered: msg.date_delivered != 0,
                cache_roomnames: msg.thread_originator_guid.clone(),
                group_title: msg.group_title,
                associated_message_guid: msg.associated_message_guid,
                associated_message_type: msg.associated_message_type,
                thread_originator_guid: msg.thread_originator_guid,
            };

            messages.push(py_msg);
        }

        Ok(messages)
    }

    /// Get all messages (use with caution on large databases)
    fn get_all_messages(&self, limit: Option<usize>) -> PyResult<Vec<PyMessage>> {
        self.query_messages_after(0.0, limit)
    }

    /// Get handle (contact) information by ID
    fn get_handle(&self, handle_id: i32) -> PyResult<Option<PyHandle>> {
        let mut stmt = self.conn.prepare(
            "SELECT rowid, id, service, uncanonicalized_id FROM handle WHERE rowid = ?"
        ).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to prepare handle query: {}", e)
            )
        })?;

        let handle = stmt.query_row([handle_id], |row| {
            Ok(PyHandle {
                rowid: row.get(0)?,
                id: row.get(1)?,
                service: row.get(2)?,
                uncanonicalized_id: row.get(3)?,
            })
        }).optional().map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to fetch handle: {}", e)
            )
        })?;

        Ok(handle)
    }

    /// Get all handles (contacts)
    fn get_all_handles(&self) -> PyResult<Vec<PyHandle>> {
        let mut stmt = self.conn.prepare(
            "SELECT rowid, id, service, uncanonicalized_id FROM handle ORDER BY rowid"
        ).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to prepare handles query: {}", e)
            )
        })?;

        let handles = stmt.query_map([], |row| {
            Ok(PyHandle {
                rowid: row.get(0)?,
                id: row.get(1)?,
                service: row.get(2)?,
                uncanonicalized_id: row.get(3)?,
            })
        }).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to execute handles query: {}", e)
            )
        })?;

        let mut result = Vec::new();
        for handle in handles {
            result.push(handle.map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("Failed to read handle: {}", e)
                )
            })?);
        }

        Ok(result)
    }

    /// Get message participants (for group messages)
    fn get_message_participants(&self, message_rowid: i32) -> PyResult<Vec<PyHandle>> {
        let mut stmt = self.conn.prepare(
            "SELECT DISTINCT h.rowid, h.id, h.service, h.uncanonicalized_id
             FROM handle h
             INNER JOIN chat_handle_join chj ON h.rowid = chj.handle_id
             INNER JOIN chat_message_join cmj ON chj.chat_id = cmj.chat_id
             WHERE cmj.message_id = ?"
        ).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to prepare participants query: {}", e)
            )
        })?;

        let handles = stmt.query_map([message_rowid], |row| {
            Ok(PyHandle {
                rowid: row.get(0)?,
                id: row.get(1)?,
                service: row.get(2)?,
                uncanonicalized_id: row.get(3)?,
            })
        }).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to execute participants query: {}", e)
            )
        })?;

        let mut result = Vec::new();
        for handle in handles {
            result.push(handle.map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("Failed to read participant: {}", e)
                )
            })?);
        }

        Ok(result)
    }

    /// Get message attachments
    fn get_message_attachments(&self, message_rowid: i32) -> PyResult<Vec<PyAttachment>> {
        let mut stmt = self.conn.prepare(
            "SELECT a.rowid, a.guid, a.filename, a.mime_type, a.transfer_name, a.total_bytes
             FROM attachment a
             INNER JOIN message_attachment_join maj ON a.rowid = maj.attachment_id
             WHERE maj.message_id = ?"
        ).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to prepare attachments query: {}", e)
            )
        })?;

        let attachments = stmt.query_map([message_rowid], |row| {
            Ok(PyAttachment {
                rowid: row.get(0)?,
                guid: row.get(1)?,
                filename: row.get(2)?,
                mime_type: row.get(3)?,
                transfer_name: row.get(4)?,
                total_bytes: row.get(5)?,
            })
        }).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                format!("Failed to execute attachments query: {}", e)
            )
        })?;

        let mut result = Vec::new();
        for attachment in attachments {
            result.push(attachment.map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("Failed to read attachment: {}", e)
                )
            })?);
        }

        Ok(result)
    }

    /// Convert a message to a Python dictionary with all related data
    fn message_to_dict(&self, py: Python, message_rowid: i32) -> PyResult<PyObject> {
        // Get the message
        let query = format!(
            "SELECT 
                m.*,
                c.chat_id,
                (SELECT COUNT(*) FROM message_attachment_join a WHERE m.ROWID = a.message_id) as num_attachments,
                NULL as deleted_from,
                0 as num_replies
            FROM message as m
            LEFT JOIN chat_message_join as c ON m.ROWID = c.message_id
            WHERE m.ROWID = {}",
            message_rowid
        );

        let mut msg = {
            let mut stmt = self.conn.prepare(&query).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("Failed to prepare message query: {}", e)
                )
            })?;

            let msg = stmt.query_row([], |row| {
                Message::from_row(row)
            }).map_err(|e| {
                PyErr::new::<pyo3::exceptions::PyRuntimeError, _>(
                    format!("Failed to fetch message: {}", e)
                )
            })?;
            msg
        };

        // Try to generate text if needed
        let text_conn = Connection::open_with_flags(
            &self.db_path,
            OpenFlags::SQLITE_OPEN_READ_ONLY
        ).map_err(|e| {
            PyErr::new::<pyo3::exceptions::PyIOError, _>(
                format!("Failed to open database for text extraction: {}", e)
            )
        })?;

        let message_text = if msg.text.is_none() || msg.text.as_ref().map(|s| s.is_empty()).unwrap_or(false) {
            match msg.generate_text(&text_conn) {
                Ok(text) => Some(text.to_string()),
                Err(_) => msg.text.clone()
            }
        } else {
            msg.text.clone()
        };

        // Get the handle if present
        let handle = if let Some(handle_id) = msg.handle_id {
            self.get_handle(handle_id)?
        } else {
            None
        };

        // Get participants
        let participants = self.get_message_participants(message_rowid)?;
        
        // Get attachments
        let attachments = self.get_message_attachments(message_rowid)?;

        // Build the dictionary
        let dict = PyDict::new(py);
        dict.set_item("rowid", msg.rowid)?;
        dict.set_item("guid", msg.guid)?;
        dict.set_item("text", message_text)?;
        dict.set_item("service", msg.service)?;
        dict.set_item("handle_id", msg.handle_id)?;
        dict.set_item("subject", msg.subject)?;
        dict.set_item("date", (msg.date as f64 / 1_000_000_000.0) + 978307200.0)?;
        dict.set_item("date_read", if msg.date_read != 0 {
            Some((msg.date_read as f64 / 1_000_000_000.0) + 978307200.0)
        } else {
            None
        })?;
        dict.set_item("date_delivered", if msg.date_delivered != 0 {
            Some((msg.date_delivered as f64 / 1_000_000_000.0) + 978307200.0)
        } else {
            None
        })?;
        dict.set_item("is_from_me", msg.is_from_me)?;
        dict.set_item("is_read", msg.is_read)?;
        dict.set_item("is_sent", true)?;  // Messages in the database are always sent
        dict.set_item("is_delivered", msg.date_delivered != 0)?;
        dict.set_item("cache_roomnames", msg.thread_originator_guid.clone())?;
        dict.set_item("group_title", msg.group_title)?;
        dict.set_item("associated_message_guid", msg.associated_message_guid)?;
        dict.set_item("associated_message_type", msg.associated_message_type)?;
        dict.set_item("thread_originator_guid", msg.thread_originator_guid)?;
        
        // Add related data
        dict.set_item("handle", handle.map(|h| h.into_py(py)))?;
        dict.set_item("participants", participants.into_py(py))?;
        dict.set_item("attachments", attachments.into_py(py))?;

        Ok(dict.into())
    }
}

/// A Python module for accessing iMessage databases
#[pymodule]
fn imessage_bridge(_py: Python, m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<IMessageDB>()?;
    m.add_class::<PyMessage>()?;
    m.add_class::<PyHandle>()?;
    m.add_class::<PyAttachment>()?;
    Ok(())
}