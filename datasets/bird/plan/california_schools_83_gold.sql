{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 1,
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
          "index": 33,
          "name": "$33",
          "type": "VARCHAR"
        },
        {
          "kind": "OTHER_FUNCTION",
          "operator": "ABS",
          "type": "FLOAT",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 38,
              "name": "$38",
              "type": "FLOAT"
            }
          ]
        }
      ],
      "id": "1",
      "inputs": [
        {
          "relOp": "LogicalTableScan",
          "table": "schools",
          "id": "0",
          "inputs": []
        }
      ]
    }
  ]
}