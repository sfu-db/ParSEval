{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "2",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 0,
          "name": "$0",
          "type": "INTEGER"
        },
        {
          "kind": "INPUT_REF",
          "index": 1,
          "name": "$1",
          "type": "INTEGER"
        }
      ],
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "sets",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}