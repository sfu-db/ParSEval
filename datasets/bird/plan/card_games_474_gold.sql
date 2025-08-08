{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "CASE",
      "operator": "CASE",
      "type": "CHAR",
      "operands": [
        {
          "kind": "EQUALS",
          "operator": "=",
          "type": "BOOLEAN",
          "operands": [
            {
              "kind": "INPUT_REF",
              "index": 80,
              "name": "$80",
              "type": "INTEGER"
            },
            {
              "kind": "LITERAL",
              "value": 1,
              "type": "INTEGER",
              "nullable": false,
              "precision": 10
            }
          ]
        },
        {
          "kind": "LITERAL",
          "value": "YES",
          "type": "CHAR",
          "nullable": false,
          "precision": 3
        },
        {
          "kind": "CAST",
          "operator": "CAST",
          "type": "CHAR",
          "operands": [
            {
              "kind": "LITERAL",
              "value": "NO",
              "type": "CHAR",
              "nullable": false,
              "precision": 2
            }
          ]
        }
      ]
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
            "index": 48,
            "name": "$48",
            "type": "VARCHAR"
          },
          {
            "kind": "LITERAL",
            "value": "Adarkar Valkyrie",
            "type": "VARCHAR",
            "nullable": false,
            "precision": -1
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
                "index": 78,
                "name": "$78",
                "type": "VARCHAR"
              },
              {
                "kind": "INPUT_REF",
                "index": 62,
                "name": "$62",
                "type": "VARCHAR"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "cards",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "sets",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}