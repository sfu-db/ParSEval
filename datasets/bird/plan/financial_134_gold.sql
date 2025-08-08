{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "FLOAT"
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
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 12,
          "name": "$12",
          "type": "FLOAT"
        }
      ],
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "district",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}