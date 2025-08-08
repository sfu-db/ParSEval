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
          "index": 2,
          "name": "$2",
          "type": "VARCHAR"
        },
        {
          "kind": "INPUT_REF",
          "index": 30,
          "name": "$30",
          "type": "INTEGER"
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
                "kind": "INPUT_REF",
                "index": 12,
                "name": "$12",
                "type": "INTEGER"
              },
              {
                "kind": "CAST",
                "operator": "CAST",
                "type": "INTEGER",
                "operands": [
                  {
                    "kind": "LITERAL",
                    "value": "61",
                    "type": "CHAR",
                    "nullable": false,
                    "precision": 2
                  }
                ]
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
                    "index": 1,
                    "name": "$1",
                    "type": "INTEGER"
                  },
                  {
                    "kind": "INPUT_REF",
                    "index": 9,
                    "name": "$9",
                    "type": "INTEGER"
                  }
                ]
              },
              "id": "2",
              "inputs": [
                {
                  "relOp": "LogicalTableScan",
                  "table": "Player",
                  "id": "0",
                  "inputs": []
                },
                {
                  "relOp": "LogicalTableScan",
                  "table": "Player_Attributes",
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