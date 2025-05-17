{
  "relOp": "LogicalSort",
  "sort": [
    {
      "column": 2,
      "type": "INTEGER"
    }
  ],
  "dir": [
    "DESCENDING"
  ],
  "offset": 0,
  "limit": 1,
  "id": "3",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 4,
          "name": "$4",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 5,
          "name": "$5",
          "type": "DATE"
        },
        {
          "kind": "INPUT_REF",
          "index": 2,
          "name": "$2",
          "type": "INTEGER"
        }
      ],
      "id": "2",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 1,
                "name": "$1",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 1999,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          "variableset": "[]",
          "id": "1",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "races",
              "id": "0",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}