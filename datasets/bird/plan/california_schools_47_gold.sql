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
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 35,
          "name": "$35",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 18,
          "name": "$18",
          "type": "FLOAT"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 54,
                    "name": "$54",
                    "type": "VARCHAR"
                  }
                ]
              },
              {
                "kind": "LITERAL",
                "value": 31,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          "variableset": "[]",
          "id": "3",
          "inputs": [
            {
              "relOp": "LogicalJoin",
              "joinType": "inner",
              "condition": {
                "kind": "EQUALS",
                "operator": "=",
                "type": "BOOLEAN",
                "operands": [
                  {
                    "kind": "INPUT_REF",
                    "index": 0,
                    "name": "$0",
                    "type": "VARCHAR"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 29,
                    "name": "$29",
                    "type": "VARCHAR"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "frpm",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "schools",
                  "id": "1",
                  "inputs": []
                }
              ]
            }
          ]
        }
      ]
    }
  ]
}