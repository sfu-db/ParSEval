{
  "relOp": "LogicalProject",
  "project": [
    {
      "kind": "INPUT_REF",
      "index": 2,
      "name": "$2",
      "type": "VARCHAR"
    }
  ],
  "id": "4",
  "inputs": [
    {
      "relOp": "LogicalFilter",
      "condition": {
        "kind": "AND",
        "operator": "AND",
        "type": "BOOLEAN",
        "operands": [
          {
            "kind": "LESS_THAN",
            "operator": "<",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 11,
                "name": "$11",
                "type": "INTEGER"
              },
              {
                "kind": "LITERAL",
                "value": 100,
                "type": "INTEGER",
                "nullable": false,
                "precision": 10
              }
            ]
          },
          {
            "kind": "EQUALS",
            "operator": "=",
            "type": "BOOLEAN",
            "operands": [
              {
                "kind": "INPUT_REF",
                "index": 13,
                "name": "$13",
                "type": "VARCHAR"
              },
              {
                "kind": "LITERAL",
                "value": "Brown",
                "type": "VARCHAR",
                "nullable": false,
                "precision": -1
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
                "index": 4,
                "name": "$4",
                "type": "INTEGER"
              },
              {
                "kind": "INPUT_REF",
                "index": 12,
                "name": "$12",
                "type": "INTEGER"
              }
            ]
          },
          "id": "2",
          "inputs": [
            {
              "relOp": "LogicalTableScan",
              "table": "superhero",
              "id": "0",
              "inputs": []
            },
            {
              "relOp": "LogicalTableScan",
              "table": "colour",
              "id": "1",
              "inputs": []
            }
          ]
        }
      ]
    }
  ]
}