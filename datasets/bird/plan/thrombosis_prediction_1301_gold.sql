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
  "id": "5",
  "inputs": [
    {
      "relOp": "LogicalProject",
      "project": [
        {
          "kind": "INPUT_REF",
          "index": 7,
          "name": "$7",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 26,
          "name": "$26",
          "type": "INTEGER"
        }
      ],
      "id": "4",
      "inputs": [
        {
          "relOp": "LogicalFilter",
          "condition": {
            "kind": "LESS_THAN",
            "operator": "<",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 26,
                "name": "$26",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 200,
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
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 13,
                    "name": "$13",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Examination",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Laboratory",
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